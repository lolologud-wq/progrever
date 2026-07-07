"""
Telegram bot handlers for the ProgrEVER management interface.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    filters,
)

import database as db
from database import get_status_emoji
from bot.keyboards import (
    main_menu_kb, accounts_list_kb, account_detail_kb,
    strategy_kb, confirm_delete_kb, back_to_account_kb,
    back_to_main_kb, settings_kb,
)
from bot.states import AWAIT_PHONE, AWAIT_STRATEGY, AWAIT_TRUSTED
from config import ADMIN_IDS, SESSIONS_DIR, EMOJI, STATUS_THRESHOLDS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Guards
# ─────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.effective_message.reply_text("⛔ Нет доступа.")
            return ConversationHandler.END
        return await func(update, ctx)
    return wrapper


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def score_bar(score: int) -> str:
    filled = score // 10
    return "█" * filled + "░" * (10 - filled) + f" {score}%"


def format_account_card(acc: dict) -> str:
    emoji = get_status_emoji(acc)
    strategy_name = "Мануал 1 (Трастовый)" if acc.get("strategy", 1) == 1 else "Мануал 2 (Автономный)"
    trusted_tag = " 🟣 [ТРАСТОВЫЙ]" if acc.get("is_trusted") else ""

    status_text = {
        EMOJI["green"]:  "Идеально прогрет",
        EMOJI["yellow"]: "Хорошо прогрет, нужно ещё",
        EMOJI["red"]:    "Плохо прогрет",
        EMOJI["black"]:  "Нет сессии",
        EMOJI["white"]:  "Новый аккаунт",
        EMOJI["purple"]: "Трастовый донор",
    }.get(emoji, "Неизвестно")

    lines = [
        f"{emoji} <b>{acc['phone']}</b>{trusted_tag}",
        f"Статус: {status_text}",
        f"Прогрев: {score_bar(acc.get('score', 0))}",
        f"День прогрева: {acc.get('day', 0)}",
        f"Стратегия: {strategy_name}",
    ]
    if acc.get("first_name"):
        lines.append(f"Имя: {acc['first_name']} {acc.get('last_name') or ''}")
    if acc.get("username"):
        lines.append(f"Username: @{acc['username']}")
    if acc.get("bio"):
        lines.append(f"Био: {acc['bio']}")
    if acc.get("hold_until"):
        hold_dt = datetime.fromisoformat(acc["hold_until"])
        remaining = hold_dt - datetime.now()
        if remaining.total_seconds() > 0:
            h = int(remaining.total_seconds() // 3600)
            m = int((remaining.total_seconds() % 3600) // 60)
            lines.append(f"⏳ Холд: ещё {h}ч {m}м")
    if acc.get("last_active"):
        lines.append(f"Активен: {acc['last_active'][:16]}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 <b>ProgrEVER</b> — Авто-прогревер аккаунтов Telegram\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ─────────────────────────────────────────────────────────
# Callback query router
# ─────────────────────────────────────────────────────────

@admin_only
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── Main menu ──
    if data == "main_menu":
        await query.edit_message_text(
            "🔥 <b>ProgrEVER</b> — Главное меню",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )

    # ── Accounts list ──
    elif data == "accounts_list":
        accounts = await db.get_all_accounts()
        if not accounts:
            await query.edit_message_text(
                "📭 Нет аккаунтов. Добавьте первый!",
                reply_markup=back_to_main_kb(),
            )
            return
        text = f"📋 <b>Аккаунты ({len(accounts)})</b>\n\n"
        legend = (
            f"{EMOJI['green']} Идеально  {EMOJI['yellow']} Хорошо  "
            f"{EMOJI['red']} Плохо  {EMOJI['white']} Новый\n"
            f"{EMOJI['black']} Нет сессии  {EMOJI['purple']} Трастовый"
        )
        text += legend
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=accounts_list_kb(accounts),
        )

    # ── Account detail ──
    elif data.startswith("acc_"):
        acc_id = int(data.split("_")[1])
        acc = await db.get_account(acc_id)
        if not acc:
            await query.edit_message_text("❌ Аккаунт не найден.", reply_markup=back_to_main_kb())
            return
        await query.edit_message_text(
            format_account_card(acc),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(acc.get("is_trusted"))),
        )

    # ── Logs ──
    elif data.startswith("logs_"):
        acc_id = int(data.split("_")[1])
        logs = await db.get_logs(acc_id, 15)
        if not logs:
            text = "📜 Логов пока нет."
        else:
            lines = ["📜 <b>Последние действия:</b>"]
            for lg in logs:
                ts = lg["ts"][:16]
                delta = f"(+{lg['score_delta']})" if lg["score_delta"] > 0 else ""
                lines.append(f"<code>{ts}</code> {lg['action']} {delta}\n  ↳ {lg['detail'] or '—'}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=back_to_account_kb(acc_id),
        )

    # ── Warm single account ──
    elif data.startswith("warm_"):
        acc_id = int(data.split("_")[1])
        await query.edit_message_text("⏳ Запускаю прогрев аккаунта...")
        acc = await db.get_account(acc_id)
        if not acc or not acc.get("has_session"):
            await query.edit_message_text(
                "❌ Нет сессии для прогрева.",
                reply_markup=back_to_account_kb(acc_id),
            )
            return
        asyncio.create_task(_warm_single(query, acc_id))

    # ── Toggle trusted ──
    elif data.startswith("toggle_trust_"):
        acc_id = int(data.split("_")[2])
        acc = await db.get_account(acc_id)
        new_val = 0 if acc.get("is_trusted") else 1
        await db.update_account(acc_id, is_trusted=new_val)
        label = "⭐ Аккаунт стал трастовым!" if new_val else "Трастовый статус снят."
        await query.edit_message_text(
            label, reply_markup=back_to_account_kb(acc_id)
        )

    # ── Delete confirm ──
    elif data.startswith("delete_"):
        acc_id = int(data.split("_")[1])
        await query.edit_message_text(
            "⚠️ Удалить аккаунт? Данные и логи будут стёрты.",
            reply_markup=confirm_delete_kb(acc_id),
        )

    elif data.startswith("confirm_delete_"):
        acc_id = int(data.split("_")[2])
        await db.delete_account(acc_id)
        await query.edit_message_text("🗑 Аккаунт удалён.", reply_markup=back_to_main_kb())

    # ── Strategy ──
    elif data.startswith("strategy_"):
        acc_id = int(data.split("_")[1])
        await query.edit_message_text(
            "Выберите стратегию прогрева:",
            reply_markup=strategy_kb(acc_id),
        )

    elif data.startswith("set_strategy_"):
        parts = data.split("_")
        acc_id = int(parts[2])
        strat = int(parts[3])
        await db.update_account(acc_id, strategy=strat)
        name = "Мануал 1 (Трастовый)" if strat == 1 else "Мануал 2 (Автономный)"
        await query.edit_message_text(
            f"✅ Стратегия изменена: {name}",
            reply_markup=back_to_account_kb(acc_id),
        )

    # ── Refresh account info ──
    elif data.startswith("refresh_"):
        acc_id = int(data.split("_")[1])
        asyncio.create_task(_refresh_account_info(query, acc_id))

    # ── Run full cycle ──
    elif data == "run_cycle":
        await query.edit_message_text("⏳ Запускаю цикл прогрева для всех аккаунтов...")
        asyncio.create_task(_run_full_cycle(query))

    # ── Stats ──
    elif data == "stats":
        await _show_stats(query)

    # ── Add account (redirect to /addaccount) ──
    elif data == "add_account":
        await query.edit_message_text(
            "📱 Используйте команду:\n\n"
            "<code>/addaccount</code>\n\n"
            "Для добавления нового аккаунта.",
            parse_mode="HTML",
            reply_markup=back_to_main_kb(),
        )

    # ── Settings ──
    elif data == "settings":
        await query.edit_message_text(
            "⚙️ <b>Настройки</b>",
            parse_mode="HTML",
            reply_markup=settings_kb(),
        )

    elif data == "schedule_info":
        await query.edit_message_text(
            "🕙 <b>Расписание прогрева:</b>\n\n"
            "• 10:00 (МСК) — утренний цикл\n"
            "• 18:00 (МСК) — вечерний цикл\n\n"
            "Принудительный запуск: кнопка «Запустить прогрев сейчас»",
            parse_mode="HTML",
            reply_markup=back_to_main_kb(),
        )


# ─────────────────────────────────────────────────────────
# /addaccount — conversation
# ─────────────────────────────────────────────────────────

@admin_only
async def cmd_add_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 Введите номер телефона аккаунта (в формате +79001234567):\n"
        "Или /cancel для отмены."
    )
    return AWAIT_PHONE


async def add_account_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("❌ Неверный формат. Пример: +79001234567")
        return AWAIT_PHONE

    ctx.user_data["new_phone"] = phone

    # Check if session file exists
    session_file = phone.replace("+", "")
    session_path = os.path.join(SESSIONS_DIR, session_file + ".session")
    has_session = os.path.exists(session_path)
    ctx.user_data["has_session"] = has_session
    ctx.user_data["session_file"] = session_file if has_session else None

    session_status = "✅ Сессия найдена" if has_session else "⚠️ Сессия не найдена (файл .session отсутствует)"

    await update.message.reply_text(
        f"Номер: <b>{phone}</b>\n{session_status}\n\n"
        "Выберите стратегию прогрева:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("1️⃣ Мануал 1 (Трастовый)", callback_data="new_strat_1")],
            [InlineKeyboardButton("2️⃣ Мануал 2 (Автономный)", callback_data="new_strat_2")],
        ]),
    )
    return AWAIT_STRATEGY


async def add_account_strategy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    strat = int(query.data.split("_")[-1])
    ctx.user_data["new_strategy"] = strat

    await query.edit_message_text(
        "Сделать этот аккаунт трастовым донором? (🟣)",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="new_trusted_yes"),
                InlineKeyboardButton("❌ Нет", callback_data="new_trusted_no"),
            ]
        ]),
    )
    return AWAIT_TRUSTED


async def add_account_trusted_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    is_trusted = query.data == "new_trusted_yes"
    ctx.user_data["new_trusted"] = is_trusted

    phone = ctx.user_data["new_phone"]
    strategy = ctx.user_data["new_strategy"]
    session_file = ctx.user_data.get("session_file")
    has_session = ctx.user_data.get("has_session", False)

    acc_id = await db.add_account(
        phone=phone,
        session_file=session_file,
        strategy=strategy,
        is_trusted=is_trusted,
    )
    await db.update_account(acc_id, has_session=1 if has_session else 0)

    strat_name = "Мануал 1" if strategy == 1 else "Мануал 2"
    trust_tag = " (🟣 Трастовый)" if is_trusted else ""
    session_tag = "✅ Сессия" if has_session else "⚠️ Нет сессии"

    from warming_engine import init_account_warming
    if has_session and not is_trusted:
        asyncio.create_task(init_account_warming(acc_id))

    await query.edit_message_text(
        f"✅ <b>Аккаунт добавлен!</b>\n\n"
        f"📱 {phone}{trust_tag}\n"
        f"Стратегия: {strat_name}\n"
        f"{session_tag}\n\n"
        f"{'🔥 Прогрев запущен!' if has_session and not is_trusted else '⚠️ Загрузите .session файл и перезапустите прогрев.'}",
        parse_mode="HTML",
        reply_markup=back_to_main_kb(),
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────
# /status — quick status for all accounts
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
        name = acc.get("first_name") or acc["phone"]
        score = acc.get("score", 0)
        day = acc.get("day", 0)
        lines.append(f"{emoji} {name} — {score}% (день {day})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# Background tasks
# ─────────────────────────────────────────────────────────

async def _warm_single(query, acc_id: int):
    from warming_engine import run_warming_cycle
    acc = await db.get_account(acc_id)
    trusted = await db.get_trusted_accounts()
    strategy = acc.get("strategy", 1)

    try:
        if strategy == 1:
            from strategies.manual1 import step_account_manual1
            ok = await step_account_manual1(acc, trusted)
        else:
            all_accounts = await db.get_all_accounts()
            peers = [a for a in all_accounts if a["id"] != acc_id]
            from strategies.manual2 import step_account_manual2
            ok = await step_account_manual2(acc, peers)

        acc = await db.get_account(acc_id)
        await query.edit_message_text(
            f"{'✅ Прогрев выполнен!' if ok else '⏸ Пропущен (холд или уже прогрет)'}\n\n"
            + format_account_card(acc),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(acc.get("is_trusted"))),
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка прогрева: {e}",
            reply_markup=back_to_account_kb(acc_id),
        )


async def _run_full_cycle(query):
    from warming_engine import run_manual_cycle
    try:
        await run_manual_cycle()
        accounts = await db.get_all_accounts()
        await query.edit_message_text(
            f"✅ Цикл прогрева завершён!\nАккаунтов: {len(accounts)}",
            reply_markup=main_menu_kb(),
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка: {e}", reply_markup=main_menu_kb()
        )


async def _refresh_account_info(query, acc_id: int):
    from config import API_ID, API_HASH, SESSIONS_DIR
    from pyrogram import Client as PyroClient

    acc = await db.get_account(acc_id)
    session = acc.get("session_file") or acc["phone"]
    session_path = os.path.join(SESSIONS_DIR, session + ".session")

    if not os.path.exists(session_path):
        await query.edit_message_text(
            "⚠️ Файл сессии не найден. Загрузите .session файл.",
            reply_markup=back_to_account_kb(acc_id),
        )
        return

    try:
        async with PyroClient(
            name=f"{SESSIONS_DIR}/{session}",
            api_id=API_ID,
            api_hash=API_HASH,
            no_updates=True,
        ) as client:
            me = await client.get_me()
            await db.update_account(
                acc_id,
                first_name=me.first_name or "",
                last_name=me.last_name or "",
                username=me.username or "",
                has_session=1,
            )
        acc = await db.get_account(acc_id)
        await query.edit_message_text(
            "✅ Информация обновлена!\n\n" + format_account_card(acc),
            parse_mode="HTML",
            reply_markup=account_detail_kb(acc_id, bool(acc.get("is_trusted"))),
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Ошибка подключения: {e}",
            reply_markup=back_to_account_kb(acc_id),
        )


async def _show_stats(query):
    accounts = await db.get_all_accounts()
    total = len(accounts)
    with_session = sum(1 for a in accounts if a.get("has_session"))
    trusted = sum(1 for a in accounts if a.get("is_trusted"))
    green = sum(1 for a in accounts if a.get("score", 0) >= STATUS_THRESHOLDS["green"])
    yellow = sum(1 for a in accounts if STATUS_THRESHOLDS["yellow"] <= a.get("score", 0) < STATUS_THRESHOLDS["green"])
    red = sum(1 for a in accounts if 0 < a.get("score", 0) < STATUS_THRESHOLDS["yellow"])
    avg_score = sum(a.get("score", 0) for a in accounts) // max(total, 1)

    text = (
        f"📊 <b>Статистика прогрева</b>\n\n"
        f"Всего аккаунтов: <b>{total}</b>\n"
        f"С сессией: <b>{with_session}</b>\n"
        f"Трастовых доноров: <b>{trusted}</b>\n\n"
        f"{EMOJI['green']} Идеально прогретых: <b>{green}</b>\n"
        f"{EMOJI['yellow']} Хорошо прогретых: <b>{yellow}</b>\n"
        f"{EMOJI['red']} Плохо прогретых: <b>{red}</b>\n\n"
        f"Средний прогрев: <b>{avg_score}%</b>"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_to_main_kb())


# ─────────────────────────────────────────────────────────
# Build conversation handler
# ─────────────────────────────────────────────────────────

def build_add_account_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addaccount", cmd_add_account)],
        states={
            AWAIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_phone)
            ],
            AWAIT_STRATEGY: [
                CallbackQueryHandler(add_account_strategy_cb, pattern=r"^new_strat_\d$")
            ],
            AWAIT_TRUSTED: [
                CallbackQueryHandler(add_account_trusted_cb, pattern=r"^new_trusted_(yes|no)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
