"""
Telegram bot handlers — ProgrEVER management interface.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    filters,
)

import database as db
from database import get_status_emoji, get_status_label
from bot.keyboards import (
    main_menu_kb, accounts_list_kb, account_detail_kb,
    confirm_delete_kb, back_to_main_kb, back_to_accounts_kb,
    back_to_account_kb, main_reply_kb, hold_kb, warmup_days_kb,
)
from bot.states import AWAIT_PHONE, AWAIT_CODE, AWAIT_2FA, AWAIT_TRUSTED, AWAIT_HOLD, AWAIT_WARMUP
from config import ADMIN_IDS, SESSIONS_DIR, EMOJI, STATUS_THRESHOLDS, API_ID, API_HASH

logger = logging.getLogger(__name__)

_login_clients: dict[int, tuple] = {}


# ─────────────────────────────────────────────────────────
# Guards & helpers
# ─────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.effective_message.reply_text("⛔ Нет доступа.")
            return ConversationHandler.END
        return await func(update, ctx)
    return wrapper


def mask_phone(phone: str) -> str:
    if len(phone) <= 7:
        return phone
    return phone[:4] + "***" + phone[-3:]


def _hold_status_text(acc: dict) -> str:
    hold_hours = acc.get("hold_hours")
    if hold_hours is None:
        hold_hours = 24
    if hold_hours <= 0:
        return "Холд отключён — прогрев начнётся сразу"
    return f"Отлежка запущена на {hold_hours}ч"


def format_account_card(acc: dict, logs: list[dict] = None) -> str:
    emoji      = get_status_emoji(acc)
    status     = get_status_label(acc)
    name_line  = f"@{acc['username']}" if acc.get("username") else acc["phone"]
    auto_icon  = "✅ ВКЛ" if acc.get("auto_warming", 1) else "❌ ВЫКЛ"
    trusted    = "ДА 🟣" if acc.get("is_trusted") else "НЕТ"

    warmup_days = acc.get("warmup_days") or 10
    day         = acc.get("day", 0)
    score       = acc.get("score", 0)
    progress    = min(100, round(day / warmup_days * 100)) if warmup_days else score

    lines = [
        f"{emoji} <b>{name_line}</b>",
        f"Статус:  {status}",
        f"Прогрев: {progress}%  |  День: {day}/{warmup_days}",
        f"Телефон: {mask_phone(acc['phone'])}",
        f"Авто: {auto_icon}  |  Trusted: {trusted}",
        f"Групп: {acc.get('groups_count', 0)}  |  Каналов: {acc.get('channels_joined', 0)}",
    ]

    hold_hours = acc.get("hold_hours")
    if hold_hours is None:
        hold_hours = 24
    if hold_hours > 0 and acc.get("hold_until"):
        hold_dt   = datetime.fromisoformat(acc["hold_until"])
        remaining = hold_dt - datetime.now()
        if remaining.total_seconds() > 0:
            h = int(remaining.total_seconds() // 3600)
            m = int((remaining.total_seconds() % 3600) // 60)
            lines.append(f"⏳ Холд: ещё {h}ч {m}м")
    elif hold_hours <= 0:
        lines.append("⏳ Холд: отключён")

    if acc.get("warmup_complete"):
        lines.append("✅ Прогрев завершён!")

    if logs:
        lines.append("")
        lines.append("Последние действия:")
        icons = {
            "hold_start": "🛏", "hold_over": "✅", "profile_change": "📝",
            "join_channel": "📢", "spambot": "🤖", "botfather": "🤖",
            "dm_sent": "💬", "group_msg": "💬", "create_group": "🏠",
            "send_sticker": "🎭", "first_write": "✉️", "first_write_batch": "✉️",
            "received_message": "📩", "set_avatar": "🖼", "remove_avatar": "🖼",
            "create_channel": "📺", "channel_post": "📝", "forward_post": "↗️",
            "bot_visit": "🤖", "error": "❌",
        }
        for lg in logs[:3]:
            ts     = lg["ts"][:16]
            icon   = icons.get(lg["action"], "•")
            delta  = f" (+{lg['score_delta']})" if lg.get("score_delta", 0) > 0 else ""
            detail = lg.get("detail") or lg["action"]
            lines.append(f"{icon} {detail}{delta}  {ts}")

    return "\n".join(lines)


def _trusted_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟣 Да, трастовый", callback_data="new_trusted_yes"),
            InlineKeyboardButton("❌ Нет",            callback_data="new_trusted_no"),
        ]
    ])


async def _cleanup_login_client(user_id: int):
    if user_id in _login_clients:
        client, _, _ = _login_clients.pop(user_id)
        try:
            await client.disconnect()
        except Exception:
            pass


async def _send_accounts_list_message(update: Update):
    accounts = await db.get_all_accounts()
    if not accounts:
        await update.effective_message.reply_text(
            "📭 Нет аккаунтов. Нажми «➕ Добавить аккаунт».",
            reply_markup=main_reply_kb(),
        )
        return
    legend = (
        f"{EMOJI['green']} Идеально  {EMOJI['yellow']} Хорошо  "
        f"{EMOJI['red']} Плохо  {EMOJI['white']} Новый  "
        f"{EMOJI['black']} Нет сессии  {EMOJI['purple']} Трастовый"
    )
    await update.effective_message.reply_text(
        f"📋 <b>Список аккаунтов ({len(accounts)})</b>\n{legend}",
        parse_mode="HTML",
        reply_markup=accounts_list_kb(accounts),
    )


def _build_svodka_text(accounts: list[dict]) -> str:
    total = len(accounts)
    counts: dict[str, int] = {}
    for acc in accounts:
        e = get_status_emoji(acc)
        for key, val in EMOJI.items():
            if e == val:
                counts[key] = counts.get(key, 0) + 1

    with_session = sum(1 for a in accounts if a.get("has_session"))
    auto_active  = sum(1 for a in accounts if a.get("has_session") and a.get("auto_warming", 1))
    avg_score    = sum(a.get("score", 0) for a in accounts) / max(total, 1)
    complete     = sum(1 for a in accounts if a.get("warmup_complete"))

    label_map = {
        "green":  f"{EMOJI['green']} Идеально прогретых",
        "yellow": f"{EMOJI['yellow']} Хорошо прогретых",
        "red":    f"{EMOJI['red']} Плохо прогретых",
        "white":  "⚪ Новых аккаунтов",
        "black":  f"{EMOJI['black']} Без сессии",
        "purple": f"{EMOJI['purple']} Трастовых",
    }
    status_lines = [
        f"{label_map[k]}: <b>{counts[k]}</b>"
        for k in ("green", "yellow", "red", "white", "black", "purple")
        if counts.get(k, 0)
    ]

    return (
        f"📊 <b>Сводка</b>\n\n"
        f"Всего аккаунтов: <b>{total}</b>\n"
        + "\n".join(status_lines) + "\n\n"
        f"Средний прогрев: <b>{avg_score:.1f}%</b>\n"
        f"Авто-прогрев: <b>{auto_active} из {with_session}</b>\n"
        f"Завершили прогрев: <b>{complete}</b>"
    )


# ─────────────────────────────────────────────────────────
# /start & menu text handler
# ─────────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 <b>ProgrEVER</b> — Авто-прогревер аккаунтов Telegram\n\n"
        "Используй кнопки ниже 👇",
        parse_mode="HTML",
        reply_markup=main_reply_kb(),
    )


@admin_only
async def menu_text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()

    if text == "📋 Все аккаунты":
        await _send_accounts_list_message(update)
        return

    if text == "➕ Добавить аккаунт":
        await cmd_add_account(update, ctx)
        return

    if text == "📊 Сводка":
        accounts = await db.get_all_accounts()
        await update.effective_message.reply_text(
            _build_svodka_text(accounts),
            parse_mode="HTML",
            reply_markup=main_reply_kb(),
        )
        return

    if text == "🔥 Прогреть сейчас":
        msg = await update.effective_message.reply_text("⏳ Запускаю цикл прогрева...")
        from warming_engine import run_manual_cycle
        try:
            await run_manual_cycle()
            accounts  = await db.get_all_accounts()
            auto_count = sum(1 for a in accounts if a.get("has_session") and a.get("auto_warming", 1))
            await msg.edit_text(
                f"✅ Цикл завершён.\n"
                f"Всего аккаунтов: {len(accounts)}\n"
                f"Авто-прогрев: {auto_count}",
            )
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка: {e}")
        return

    if text == "❌ Отмена":
        await cmd_cancel(update, ctx)
        return

    await update.effective_message.reply_text(
        "Используй кнопки на клавиатуре 👇",
        reply_markup=main_reply_kb(),
    )


# ─────────────────────────────────────────────────────────
# Callback router
# ─────────────────────────────────────────────────────────

@admin_only
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "main_menu":
        await query.edit_message_text(
            "🔥 <b>ProgrEVER</b> — Главное меню",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )

    elif data == "accounts_list":
        accounts = await db.get_all_accounts()
        if not accounts:
            await query.edit_message_text(
                "📭 Нет аккаунтов. Нажми «➕ Добавить аккаунт».",
                reply_markup=back_to_main_kb(),
            )
            return
        legend = (
            f"{EMOJI['green']} Идеально  {EMOJI['yellow']} Хорошо  "
            f"{EMOJI['red']} Плохо  {EMOJI['white']} Новый  "
            f"{EMOJI['black']} Нет сессии  {EMOJI['purple']} Трастовый"
        )
        await query.edit_message_text(
            f"📋 <b>Список аккаунтов ({len(accounts)})</b>\n{legend}",
            parse_mode="HTML",
            reply_markup=accounts_list_kb(accounts),
        )

    elif data.startswith("acc_"):
        acc_id = int(data.split("_")[1])
        acc    = await db.get_account(acc_id)
        if not acc:
            await query.edit_message_text("❌ Аккаунт не найден.", reply_markup=back_to_main_kb())
            return
        logs = await db.get_logs(acc_id, 3)
        await query.edit_message_text(
            format_account_card(acc, logs),
            parse_mode="HTML",
            reply_markup=account_detail_kb(
                acc_id,
                auto_on=bool(acc.get("auto_warming", 1)),
                is_trusted=bool(acc.get("is_trusted")),
            ),
        )

    elif data.startswith("toggle_auto_"):
        acc_id  = int(data.split("_")[2])
        acc     = await db.get_account(acc_id)
        new_val = 0 if acc.get("auto_warming", 1) else 1
        await db.update_account(acc_id, auto_warming=new_val)
        label = "▶️ Авто-прогрев включён!" if new_val else "⏸ Авто-прогрев выключен."
        acc  = await db.get_account(acc_id)
        logs = await db.get_logs(acc_id, 3)
        await query.edit_message_text(
            f"{label}\n\n" + format_account_card(acc, logs),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(new_val), bool(acc.get("is_trusted"))),
        )

    elif data.startswith("toggle_trust_"):
        acc_id  = int(data.split("_")[2])
        acc     = await db.get_account(acc_id)
        new_val = 0 if acc.get("is_trusted") else 1
        await db.update_account(acc_id, is_trusted=new_val)
        label = "🟣 Трастовый статус включён!" if new_val else "Трастовый статус снят."
        acc  = await db.get_account(acc_id)
        logs = await db.get_logs(acc_id, 3)
        await query.edit_message_text(
            f"{label}\n\n" + format_account_card(acc, logs),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(acc.get("auto_warming", 1)), bool(new_val)),
        )

    elif data.startswith("delete_"):
        acc_id = int(data.split("_")[1])
        await query.edit_message_text(
            "⚠️ Удалить аккаунт? Все данные и логи будут стёрты.",
            reply_markup=confirm_delete_kb(acc_id),
        )

    elif data.startswith("confirm_delete_"):
        acc_id = int(data.split("_")[2])
        await db.delete_account(acc_id)
        await query.edit_message_text("🗑 Аккаунт удалён.", reply_markup=back_to_main_kb())

    elif data.startswith("do_spambot_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("🤖 Запускаю SpamBot...")
        asyncio.create_task(_action_spambot(query, acc_id))

    elif data.startswith("do_join_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("📢 Вступаю в канал...")
        asyncio.create_task(_action_join_channel(query, acc_id))

    elif data.startswith("do_grpmsg_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("💬 Отправляю сообщение в группу...")
        asyncio.create_task(_action_group_msg(query, acc_id))

    elif data.startswith("do_profile_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("📝 Обновляю профиль...")
        asyncio.create_task(_action_update_profile(query, acc_id))

    elif data.startswith("do_hold_"):
        acc_id = int(data.split("_")[2])
        acc    = await db.get_account(acc_id)
        from strategies.warmer import do_hold
        await do_hold(acc)
        acc  = await db.get_account(acc_id)
        logs = await db.get_logs(acc_id, 3)
        await query.edit_message_text(
            f"🛏 {_hold_status_text(acc)}.\n\n" + format_account_card(acc, logs),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(acc.get("auto_warming", 1)), bool(acc.get("is_trusted"))),
        )

    elif data.startswith("do_create_grp_"):
        acc_id = int(data.split("_")[3])
        await query.edit_message_text("🏠 Создаю группу...")
        asyncio.create_task(_action_create_group(query, acc_id))

    elif data.startswith("do_channel_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("📺 Создаю канал...")
        asyncio.create_task(_action_create_channel(query, acc_id))

    elif data.startswith("do_chpost_"):
        acc_id = int(data.split("_")[2])
        await query.edit_message_text("📬 Пишу в канал...")
        asyncio.create_task(_action_channel_post(query, acc_id))

    elif data == "run_cycle":
        await query.edit_message_text("⏳ Запускаю цикл прогрева для всех аккаунтов...")
        asyncio.create_task(_run_full_cycle(query))

    elif data == "svodka":
        accounts = await db.get_all_accounts()
        await query.edit_message_text(
            _build_svodka_text(accounts),
            parse_mode="HTML",
            reply_markup=back_to_main_kb(),
        )

    elif data == "add_account":
        await query.message.reply_text(
            "📱 Нажми кнопку «➕ Добавить аккаунт» на клавиатуре снизу.",
            reply_markup=main_reply_kb(),
        )


# ─────────────────────────────────────────────────────────
# Add-account conversation
# phone → [code] → [2fa] → trusted? → hold? → warmup? → done
# ─────────────────────────────────────────────────────────

@admin_only
async def cmd_add_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 Введите номер телефона в формате <code>+79001234567</code>\n\n"
        "Для отмены нажми «❌ Отмена».",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Отмена")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return AWAIT_PHONE


async def add_account_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from pyrogram import Client as PyroClient
    from pyrogram.errors import FloodWait, PhoneNumberInvalid, PhoneNumberBanned

    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return AWAIT_PHONE

    ctx.user_data["new_phone"] = phone
    user_id      = update.effective_user.id
    session_file = phone.replace("+", "")
    session_path = os.path.join(SESSIONS_DIR, session_file + ".session")

    if os.path.exists(session_path):
        ctx.user_data["has_session"]  = True
        ctx.user_data["session_file"] = session_file
        await update.message.reply_text(
            f"✅ Сессия для <b>{phone}</b> уже существует.\n\n"
            "Сделать аккаунт трастовым донором? 🟣",
            parse_mode="HTML",
            reply_markup=_trusted_kb(),
        )
        return AWAIT_TRUSTED

    msg = await update.message.reply_text(
        f"📡 Подключаюсь к Telegram для <b>{phone}</b>...",
        parse_mode="HTML",
    )
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        client = PyroClient(
            name=os.path.join(SESSIONS_DIR, session_file),
            api_id=API_ID,
            api_hash=API_HASH,
        )
        await client.connect()
        sent = await client.send_code(phone)
        _login_clients[user_id] = (client, phone, sent.phone_code_hash)
        await msg.edit_text(
            f"📨 Код отправлен на <b>{phone}</b>\n\n"
            "Введите код из Telegram (только цифры):",
            parse_mode="HTML",
        )
        return AWAIT_CODE
    except FloodWait as e:
        await msg.edit_text(f"⏳ Флуд-лимит. Подождите <b>{e.value}</b> сек.", parse_mode="HTML")
    except PhoneNumberBanned:
        await msg.edit_text("🚫 Номер заблокирован в Telegram.")
    except PhoneNumberInvalid:
        await msg.edit_text("❌ Номер не распознан Telegram.")
        return AWAIT_PHONE
    except Exception as ex:
        logger.error(f"Login error {phone}: {ex}")
        await msg.edit_text(f"❌ Ошибка: <code>{ex}</code>", parse_mode="HTML")
    return ConversationHandler.END


async def add_account_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired

    user_id = update.effective_user.id
    code    = re.sub(r"\D", "", update.message.text.strip())

    if not code:
        await update.message.reply_text("❌ Введите только цифры кода.")
        return AWAIT_CODE

    if user_id not in _login_clients:
        await update.message.reply_text("❌ Сессия истекла. Начни заново.")
        return ConversationHandler.END

    client, phone, phone_code_hash = _login_clients[user_id]
    try:
        await client.sign_in(phone, phone_code_hash, code)
        await client.disconnect()
        del _login_clients[user_id]
        ctx.user_data["has_session"]  = True
        ctx.user_data["session_file"] = phone.replace("+", "")
        await update.message.reply_text(
            "✅ <b>Авторизация успешна!</b>\n\nСделать аккаунт трастовым донором? 🟣",
            parse_mode="HTML",
            reply_markup=_trusted_kb(),
        )
        return AWAIT_TRUSTED
    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 Включена 2FA. Введите облачный пароль:"
        )
        return AWAIT_2FA
    except PhoneCodeInvalid:
        await update.message.reply_text("❌ Неверный код. Попробуйте снова:")
        return AWAIT_CODE
    except PhoneCodeExpired:
        await _cleanup_login_client(user_id)
        await update.message.reply_text("⌛ Код устарел. Начни заново.")
        return ConversationHandler.END
    except Exception as ex:
        logger.error(f"sign_in error {phone}: {ex}")
        await _cleanup_login_client(user_id)
        await update.message.reply_text(f"❌ Ошибка: <code>{ex}</code>", parse_mode="HTML")
        return ConversationHandler.END


async def add_account_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    password = update.message.text.strip()

    if user_id not in _login_clients:
        await update.message.reply_text("❌ Сессия истекла. Начни заново.")
        return ConversationHandler.END

    client, phone, _ = _login_clients[user_id]
    try:
        await client.check_password(password)
        await client.disconnect()
        del _login_clients[user_id]
        ctx.user_data["has_session"]  = True
        ctx.user_data["session_file"] = phone.replace("+", "")
        await update.message.reply_text(
            "✅ <b>Вход с паролем выполнен!</b>\n\nСделать аккаунт трастовым донором? 🟣",
            parse_mode="HTML",
            reply_markup=_trusted_kb(),
        )
        return AWAIT_TRUSTED
    except Exception as ex:
        await update.message.reply_text(
            f"❌ Неверный пароль: <code>{ex}</code>\nПопробуйте снова:",
            parse_mode="HTML",
        )
        return AWAIT_2FA


async def add_account_trusted_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["new_trusted"] = (query.data == "new_trusted_yes")

    await query.edit_message_text(
        "⏳ <b>Холд перед прогревом</b>\n\n"
        "Выбери время отлежки (аккаунт будет неактивен):",
        parse_mode="HTML",
        reply_markup=hold_kb(),
    )
    return AWAIT_HOLD


async def add_account_hold(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles both callback (preset) and text (custom) for hold hours."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        val = query.data  # hold_0 / hold_12 / hold_24 / hold_48 / hold_custom

        if val == "hold_custom":
            await query.edit_message_text(
                "✏️ Введите количество часов холда (например: <code>6</code>):",
                parse_mode="HTML",
            )
            ctx.user_data["awaiting_hold_text"] = True
            return AWAIT_HOLD

        hours = int(val.split("_")[1])
        ctx.user_data["new_hold_hours"] = hours
        await query.edit_message_text(
            f"✅ Холд: <b>{hours}ч</b>\n\n"
            "📅 <b>Длительность прогрева</b>\n"
            "Выбери сколько дней прогревать аккаунт:",
            parse_mode="HTML",
            reply_markup=warmup_days_kb(),
        )
        return AWAIT_WARMUP

    # Text input (custom hours)
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) < 0:
        await update.message.reply_text("❌ Введите целое число часов (например 6):")
        return AWAIT_HOLD

    ctx.user_data["new_hold_hours"]      = int(text)
    ctx.user_data["awaiting_hold_text"]  = False
    await update.message.reply_text(
        f"✅ Холд: <b>{text}ч</b>\n\n"
        "📅 <b>Длительность прогрева</b>\n"
        "Выбери сколько дней прогревать аккаунт:",
        parse_mode="HTML",
        reply_markup=warmup_days_kb(),
    )
    return AWAIT_WARMUP


async def add_account_warmup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles both callback (preset) and text (custom) for warmup days."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        val = query.data  # warmup_3 / warmup_5 / ... / warmup_custom

        if val == "warmup_custom":
            await query.edit_message_text(
                "✏️ Введите количество дней прогрева (например: <code>10</code>):",
                parse_mode="HTML",
            )
            ctx.user_data["awaiting_warmup_text"] = True
            return AWAIT_WARMUP

        days = int(val.split("_")[1])
        return await _finish_add_account(query.message, ctx, days, via_query=query)

    # Text input (custom days)
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ Введите целое число дней (например 10):")
        return AWAIT_WARMUP

    return await _finish_add_account(update.message, ctx, int(text))


async def _finish_add_account(message, ctx, warmup_days: int, via_query=None):
    phone        = ctx.user_data["new_phone"]
    session_file = ctx.user_data.get("session_file")
    has_session  = ctx.user_data.get("has_session", False)
    is_trusted   = ctx.user_data.get("new_trusted", False)
    hold_hours   = ctx.user_data.get("new_hold_hours", 24)

    acc_id = await db.add_account(
        phone=phone,
        session_file=session_file,
        strategy=1,
        is_trusted=is_trusted,
        hold_hours=hold_hours,
        warmup_days=warmup_days,
    )
    updates = {
        "has_session": 1 if has_session else 0,
        "hold_hours": hold_hours,
        "warmup_days": warmup_days,
    }
    if hold_hours <= 0:
        updates["hold_until"] = None
    await db.update_account(acc_id, **updates)

    trust_tag   = " (🟣 Трастовый)" if is_trusted else ""
    session_tag = "✅ Сессия активна" if has_session else "⚠️ Нет сессии"

    from warming_engine import init_account_warming
    if has_session and not is_trusted:
        asyncio.create_task(init_account_warming(acc_id))

    text = (
        f"✅ <b>Аккаунт добавлен!</b>\n\n"
        f"📱 {phone}{trust_tag}\n"
        f"{session_tag}\n"
        f"⏳ Холд: {hold_hours}ч\n"
        f"📅 Прогрев: {warmup_days} дней\n\n"
        f"{'🔥 Прогрев запущен!' if has_session and not is_trusted else '⚠️ Нужен .session файл для запуска.'}"
    )
    if via_query:
        await via_query.edit_message_text(text, parse_mode="HTML", reply_markup=back_to_main_kb())
    else:
        await message.reply_text(text, parse_mode="HTML", reply_markup=main_reply_kb())

    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _cleanup_login_client(update.effective_user.id)
    await update.message.reply_text("❌ Отменено.", reply_markup=main_reply_kb())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# /status
# ─────────────────────────────────────────────────────────

@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    accounts = await db.get_all_accounts()
    if not accounts:
        await update.message.reply_text("Нет аккаунтов.")
        return
    lines = ["📊 <b>Статус аккаунтов:</b>\n"]
    for acc in accounts:
        emoji = get_status_emoji(acc)
        name  = f"@{acc['username']}" if acc.get("username") else acc["phone"]
        day   = acc.get("day", 0)
        wd    = acc.get("warmup_days") or 10
        pct   = min(100, round(day / wd * 100)) if wd else 0
        auto  = "▶" if acc.get("auto_warming", 1) else "⏸"
        lines.append(f"{emoji} {name} — {pct}% (день {day}/{wd}) {auto}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# Background action helpers
# ─────────────────────────────────────────────────────────

async def _reload_and_show(query, acc_id: int, prefix: str = ""):
    acc  = await db.get_account(acc_id)
    logs = await db.get_logs(acc_id, 3)
    text = (prefix + "\n\n" + format_account_card(acc, logs)).strip()
    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=account_detail_kb(
            acc_id,
            auto_on=bool(acc.get("auto_warming", 1)),
            is_trusted=bool(acc.get("is_trusted")),
        ),
    )


async def _action_spambot(query, acc_id: int):
    from strategies.warmer import do_spambot
    acc = await db.get_account(acc_id)
    try:
        ok = await do_spambot(acc)
        await _reload_and_show(query, acc_id, "✅ SpamBot готово!" if ok else "⚠️ SpamBot: ошибка.")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_join_channel(query, acc_id: int):
    from strategies.warmer import do_join_channel
    acc = await db.get_account(acc_id)
    try:
        joined = await asyncio.wait_for(
            do_join_channel(acc, count=1, with_delay=False, max_flood_wait=25),
            timeout=45,
        )
        await _reload_and_show(query, acc_id, f"✅ Вступил в {joined} канал(ов).")
    except asyncio.TimeoutError:
        await _reload_and_show(query, acc_id, "⏱ Операция заняла долго, пропущено.")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_group_msg(query, acc_id: int):
    from strategies.warmer import do_group_message
    import random
    acc    = await db.get_account(acc_id)
    groups = await db.get_all_groups()
    if not groups:
        await _reload_and_show(query, acc_id, "⚠️ Нет групп. Сначала создайте группу.")
        return
    chat_id = random.choice(groups).get("chat_id")
    try:
        ok = await do_group_message(acc, chat_id)
        await _reload_and_show(query, acc_id, "✅ Сообщение в группу отправлено!" if ok else "⚠️ Ошибка.")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_update_profile(query, acc_id: int):
    from strategies.warmer import do_update_profile
    acc = await db.get_account(acc_id)
    try:
        ok = await do_update_profile(acc)
        await _reload_and_show(query, acc_id, "✅ Профиль обновлён!" if ok else "⚠️ Ошибка.")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_create_group(query, acc_id: int):
    from strategies.warmer import do_create_group
    import random as _random
    acc          = await db.get_account(acc_id)
    all_accounts = await db.get_all_accounts()
    peers = [a for a in all_accounts if a["id"] != acc_id and a.get("has_session")]
    if not peers:
        await _reload_and_show(query, acc_id, "⚠️ Нет других аккаунтов для группы.")
        return
    members = _random.sample(peers, min(_random.randint(2, 5), len(peers)))
    try:
        chat_id = await do_create_group(acc, members)
        msg = f"✅ Группа создана! ID: {chat_id}" if chat_id else "⚠️ Не удалось создать группу."
        await _reload_and_show(query, acc_id, msg)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_create_channel(query, acc_id: int):
    from strategies.warmer import do_create_own_channel
    acc = await db.get_account(acc_id)
    try:
        ch_id = await do_create_own_channel(acc)
        msg = f"✅ Канал создан! ID: {ch_id}" if ch_id else "⚠️ Не удалось создать канал."
        await _reload_and_show(query, acc_id, msg)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _action_channel_post(query, acc_id: int):
    from strategies.warmer import do_post_to_own_channel
    acc = await db.get_account(acc_id)
    try:
        ok = await do_post_to_own_channel(acc)
        await _reload_and_show(query, acc_id, "✅ Пост опубликован!" if ok else "⚠️ Нет личного канала. Сначала создайте.")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=back_to_account_kb(acc_id))


async def _run_full_cycle(query):
    from warming_engine import run_manual_cycle
    try:
        await run_manual_cycle()
        accounts   = await db.get_all_accounts()
        auto_count = sum(1 for a in accounts if a.get("has_session") and a.get("auto_warming", 1))
        await query.edit_message_text(
            f"✅ Цикл прогрева завершён!\n"
            f"Всего аккаунтов: {len(accounts)}\n"
            f"Авто-прогрев: {auto_count}",
            reply_markup=main_menu_kb(),
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=main_menu_kb())


# ─────────────────────────────────────────────────────────
# Build conversation handler
# ─────────────────────────────────────────────────────────

def build_add_account_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addaccount", cmd_add_account),
            MessageHandler(filters.Regex(r"^➕ Добавить аккаунт$"), cmd_add_account),
            CallbackQueryHandler(lambda u, c: None, pattern=r"^add_account$"),
        ],
        states={
            AWAIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_phone)
            ],
            AWAIT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_code)
            ],
            AWAIT_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_2fa)
            ],
            AWAIT_TRUSTED: [
                CallbackQueryHandler(add_account_trusted_cb, pattern=r"^new_trusted_(yes|no)$")
            ],
            AWAIT_HOLD: [
                CallbackQueryHandler(add_account_hold, pattern=r"^hold_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_hold),
            ],
            AWAIT_WARMUP: [
                CallbackQueryHandler(add_account_warmup, pattern=r"^warmup_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_warmup),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            MessageHandler(filters.Regex(r"^❌ Отмена$"), cmd_cancel),
        ],
        allow_reentry=True,
    )
