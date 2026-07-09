"""
strategies/warmer.py — unified warming strategy.

Implements all warming actions:
  - hold (configurable per account)
  - profile change
  - join public channels
  - send sticker / DM to trusted
  - create groups (random subset)
  - send group messages
  - create personal channels + post content
  - forward channel posts with comments
  - visit bots
  - send status messages (night / morning / busy)
  - respond to pending messages
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.errors import (
    FloodWait, ChannelPrivate, PeerFlood,
    UserBannedInChannel, BadRequest, UserPrivacyRestricted,
    UserNotMutualContact,
)

import database as db
from config import (
    SESSIONS_DIR, API_ID, API_HASH,
    WARM_CHANNELS, ALL_BOTS,
    RANDOM_FIRST_NAMES, RANDOM_LAST_NAMES, CONTACT_LABELS,
    CHANNEL_POST_TEMPLATES, CHANNEL_PHOTO_CAPTIONS, CHANNEL_NAMES,
    FORWARD_COMMENTS, REPLY_MESSAGES, SLEEP_MESSAGES, MORNING_MESSAGES, BUSY_MESSAGES,
    DELAY_MIN, DELAY_MAX,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _session(account: dict) -> str:
    sf = account.get("session_file") or account["phone"].replace("+", "")
    if sf.endswith(".session"):
        sf = sf[:-8]
    return os.path.join(SESSIONS_DIR, sf)


def _make_client(account: dict) -> Client:
    return Client(
        name=_session(account),
        api_id=API_ID,
        api_hash=API_HASH,
    )


def _random_name(seed_id: int = 0) -> str:
    """Return a random contact label like 'Друг Коли', 'Папа Миши' etc."""
    rng   = random.Random(seed_id + random.randint(0, 999))
    first = rng.choice(RANDOM_FIRST_NAMES)
    label = rng.choice(CONTACT_LABELS)
    return label.format(name=first)


async def _safe_flood(coro, max_wait: int = 60):
    """Await coroutine, sleep through FloodWait up to max_wait seconds."""
    while True:
        try:
            return await coro
        except FloodWait as e:
            if e.value > max_wait:
                logger.warning(f"FloodWait {e.value}s > limit {max_wait}s — skipping")
                return None
            await asyncio.sleep(e.value + 3)


# ─────────────────────────────────────────────────────────
# Hold
# ─────────────────────────────────────────────────────────

def _get_hold_hours(account: dict) -> int:
    """Return configured hold hours; 0 means disabled."""
    val = account.get("hold_hours")
    if val is None:
        return 24
    return int(val)


async def do_hold(account: dict) -> None:
    hold_hours = _get_hold_hours(account)
    if hold_hours <= 0:
        await db.update_account(account["id"], hold_until=None)
        await db.log_action(account["id"], "hold_skip", "Холд отключён", 0)
        logger.info(f"[{account['phone']}] hold disabled (0h)")
        return
    hold_until = (datetime.now() + timedelta(hours=hold_hours)).isoformat()
    await db.update_account(account["id"], hold_until=hold_until)
    await db.log_action(account["id"], "hold_start", f"Отлежка {hold_hours}ч", 0)
    logger.info(f"[{account['phone']}] hold for {hold_hours}h until {hold_until}")


def _is_in_hold(account: dict) -> bool:
    if _get_hold_hours(account) <= 0:
        return False
    hu = account.get("hold_until")
    if not hu:
        return False
    try:
        return datetime.fromisoformat(hu) > datetime.now()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────
# Profile update
# ─────────────────────────────────────────────────────────

_BIOS = [
    "Просто живу и радуюсь 🙂",
    "Любитель кофе и хороших книг ☕",
    "Работаю, отдыхаю, повторяю",
    "Не спешу, но всегда успеваю",
    "Здесь редко, но метко",
    "Из Москвы с любовью 🌆",
    "Музыка — моя жизнь 🎵",
    "Путешественник-любитель ✈️",
    "Foodie & cat person 🐱",
    "Живу по принципу: меньше слов, больше дела",
]

_FIRST_NAMES = [
    "Александр", "Михаил", "Сергей", "Дмитрий", "Андрей",
    "Наталья", "Ирина", "Елена", "Татьяна", "Мария",
    "Артём", "Владимир", "Максим", "Алексей", "Никита",
]

_LAST_NAMES = [
    "Иванов", "Петров", "Сидоров", "Козлов", "Николаев",
    "Соколова", "Морозова", "Попова", "Волкова", "Лебедева",
]


async def do_update_profile(account: dict) -> bool:
    client = _make_client(account)
    rng    = random.Random(account["id"])
    try:
        await client.start()
        first = rng.choice(_FIRST_NAMES)
        last  = rng.choice(_LAST_NAMES)
        bio   = rng.choice(_BIOS)
        await _safe_flood(client.update_profile(first_name=first, last_name=last, bio=bio))
        await db.log_action(account["id"], "profile_change", f"{first} {last} | {bio}", 3)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] profile update: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# SpamBot / BotFather
# ─────────────────────────────────────────────────────────

async def do_spambot(account: dict) -> bool:
    client = _make_client(account)
    try:
        await client.start()
        peer = await client.resolve_peer("SpamBot")
        await asyncio.sleep(random.uniform(1, 3))
        await client.send_message("SpamBot", "/start")
        await asyncio.sleep(random.uniform(5, 15))
        async for msg in client.get_chat_history("SpamBot", limit=3):
            if msg.reply_markup:
                for row in msg.reply_markup.inline_keyboard if hasattr(msg.reply_markup, "inline_keyboard") else []:
                    for btn in row:
                        if hasattr(btn, "callback_data") and btn.callback_data:
                            await asyncio.sleep(random.uniform(2, 6))
                            try:
                                await msg.click(btn.callback_data)
                            except Exception:
                                pass
                            break
                break
        await db.log_action(account["id"], "spambot", "Написал в SpamBot", 2)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] spambot: {ex}")
        await db.log_action(account["id"], "spambot", f"Ошибка SpamBot: {ex}", 0)
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Visit random bot
# ─────────────────────────────────────────────────────────

async def do_visit_random_bot(account: dict) -> bool:
    bot_name = random.choice(ALL_BOTS)
    client   = _make_client(account)
    try:
        await client.start()
        await asyncio.sleep(random.uniform(2, 5))
        await client.send_message(bot_name, "/start")
        await asyncio.sleep(random.uniform(5, 20))
        async for msg in client.get_chat_history(bot_name, limit=5):
            if msg.reply_markup:
                keyboard = getattr(msg.reply_markup, "inline_keyboard", None)
                if keyboard:
                    for row in keyboard:
                        for btn in row:
                            if hasattr(btn, "callback_data") and btn.callback_data:
                                try:
                                    await asyncio.sleep(random.uniform(2, 6))
                                    await msg.click(btn.callback_data)
                                except Exception:
                                    pass
                                break
                        break
                break
        msg_text = random.choice(["Привет!", "Помощь", "Что умеешь?", "Начать"])
        await asyncio.sleep(random.uniform(3, 8))
        await client.send_message(bot_name, msg_text)
        await db.log_action(account["id"], "bot_visit", f"Посетил @{bot_name}", 1)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] bot_visit {bot_name}: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Join public channel
# ─────────────────────────────────────────────────────────

async def do_join_channel(
    account: dict,
    count: int = 2,
    with_delay: bool = True,
    max_flood_wait: int = 60,
) -> int:
    client  = _make_client(account)
    joined  = 0
    shuffle = WARM_CHANNELS[:]
    random.shuffle(shuffle)
    try:
        await client.start()
        for ch in shuffle[:count + 4]:
            if joined >= count:
                break
            try:
                if with_delay:
                    await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                await _safe_flood(client.join_chat(ch), max_wait=max_flood_wait)
                await db.log_action(account["id"], "join_channel", f"@{ch}", 3)
                await db.add_score(account["id"], 3)
                joined += 1
                acc = await db.get_account(account["id"])
                await db.update_account(
                    account["id"],
                    channels_joined=acc.get("channels_joined", 0) + 1,
                )
            except (ChannelPrivate, UserBannedInChannel, BadRequest):
                continue
            except FloodWait as e:
                if e.value > max_flood_wait:
                    break
                await asyncio.sleep(e.value + 2)
    except Exception as ex:
        logger.error(f"[{account['phone']}] join_channel: {ex}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return joined


# ─────────────────────────────────────────────────────────
# Forward channel post to DM with comment
# ─────────────────────────────────────────────────────────

async def do_forward_channel_post(account: dict, target_accounts: list[dict]) -> bool:
    if not target_accounts:
        return False
    shuffle = WARM_CHANNELS[:]
    random.shuffle(shuffle)
    client  = _make_client(account)
    try:
        await client.start()
        for ch_name in shuffle[:5]:
            try:
                msgs = []
                async for m in client.get_chat_history(ch_name, limit=20):
                    if m.text or m.caption:
                        msgs.append(m)
                if not msgs:
                    continue
                msg    = random.choice(msgs)
                target = random.choice(target_accounts)
                comment = random.choice(FORWARD_COMMENTS)
                await asyncio.sleep(random.uniform(10, 30))
                await client.forward_messages(target["phone"], ch_name, msg.id)
                await asyncio.sleep(random.uniform(5, 15))
                await client.send_message(target["phone"], comment)
                await db.log_action(account["id"], "forward_post", f"из @{ch_name} → {target['phone']}", 2)
                return True
            except Exception:
                continue
    except Exception as ex:
        logger.error(f"[{account['phone']}] forward_post: {ex}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────────────────
# Create / post to personal channel
# ─────────────────────────────────────────────────────────

def _channel_name_for(account: dict) -> str:
    rng      = random.Random(account["id"])
    template = rng.choice(CHANNEL_NAMES)
    first    = rng.choice(RANDOM_FIRST_NAMES)
    return template.format(name=first)


async def do_create_own_channel(account: dict) -> int | None:
    own = db.get_own_channels(account)
    if len(own) >= 2:
        logger.info(f"[{account['phone']}] already has 2 own channels")
        return None

    title  = _channel_name_for(account)
    client = _make_client(account)
    try:
        await client.start()
        ch    = await _safe_flood(client.create_channel(title, "Мой личный канал 📝"))
        if not ch:
            return None
        ch_id = ch.id
        own.append(ch_id)
        await db.save_own_channels(account["id"], own)
        await db.log_action(account["id"], "create_channel", f"Создал канал «{title}»", 5)
        logger.info(f"[{account['phone']}] created channel {ch_id}")
        return ch_id
    except Exception as ex:
        logger.error(f"[{account['phone']}] create_channel: {ex}")
        return None
    finally:
        try:
            await client.stop()
        except Exception:
            pass


async def do_post_to_own_channel(account: dict) -> bool:
    own = db.get_own_channels(account)
    if not own:
        return False
    ch_id  = random.choice(own)
    text   = random.choice(CHANNEL_POST_TEMPLATES)
    client = _make_client(account)
    try:
        await client.start()
        await _safe_flood(client.send_message(ch_id, text))
        await db.log_action(account["id"], "channel_post", f"Пост в канале: {text[:40]}", 2)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] channel_post: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Create group chat
# ─────────────────────────────────────────────────────────

_GROUP_TITLES = [
    "Огородники 🌱", "Одногруппники 📚", "Друзья",
    "ДР 07.25 🎂", "Рабочий чат 💼", "Наша тусовка 🎉",
    "Семья 🏡", "Спортики 💪", "Чатик выходного дня",
    "Вечерние посиделки 🍕",
]


async def do_create_group(account: dict, members: list[dict]) -> int | None:
    title    = random.choice(_GROUP_TITLES)
    client   = _make_client(account)
    user_ids = []
    try:
        await client.start()
        for m in members:
            try:
                peer = await client.resolve_peer(m["phone"])
                user_ids.append(peer.user_id)
            except Exception:
                continue

        if not user_ids:
            await db.log_action(account["id"], "create_group_skip", "Нет доступных участников", 0)
            return None

        tried_ids = user_ids[:9]
        chat = None

        try:
            chat = await client.create_group(title, tried_ids)
        except BadRequest as ex:
            if "CHAT_MEMBER_ADD_FAILED" in str(ex):
                for uid in tried_ids:
                    try:
                        chat = await client.create_group(title, [uid])
                        break
                    except BadRequest as se:
                        if "CHAT_MEMBER_ADD_FAILED" in str(se):
                            continue
                        raise
            else:
                raise

        if chat is None:
            await db.log_action(account["id"], "create_group_skip", "Не удалось добавить участников", 0)
            return None

        chat_id  = chat.id
        group_pk = await db.add_group(chat_id, title, account["id"])
        for uid in tried_ids:
            await db.add_group_member(group_pk, account["id"])
        acc = await db.get_account(account["id"])
        await db.update_account(account["id"], groups_count=acc.get("groups_count", 0) + 1)
        await db.log_action(account["id"], "create_group", f"Создал группу «{title}»", 4)
        return chat_id

    except (PeerFlood, FloodWait) as ex:
        logger.warning(f"[{account['phone']}] create_group flood: {ex}")
        return None
    except Exception as ex:
        logger.error(f"[{account['phone']}] create_group: {ex}")
        await db.log_action(account["id"], "create_group_skip", f"Ошибка: {ex}", 0)
        return None
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Group message
# ─────────────────────────────────────────────────────────

_GROUP_MESSAGES = [
    "Привет всем! 👋",
    "Как дела, народ?",
    "Кто онлайн?",
    "Что новенького?",
    "Всем привет 🙂",
    "Хорошего дня всем!",
    "Ребят, как жизнь?",
    "Ну как вы там?",
    "Заходил проверить 😄",
    "Напишите, как дела!",
    "Долго не появлялся, привет!",
    "Кто что делает?",
]


async def do_group_message(account: dict, chat_id: int) -> bool:
    text   = random.choice(_GROUP_MESSAGES)
    client = _make_client(account)
    try:
        await client.start()
        await _safe_flood(client.send_message(chat_id, text))
        await db.log_action(account["id"], "group_msg", f"В группу: {text[:40]}", 2)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] group_msg: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Send sticker
# ─────────────────────────────────────────────────────────

_STICKER_SETS = [
    "HotCherry", "EvilMinds", "CatsCoffee",
    "ClassicHappy", "Animals", "BORED",
]


async def do_send_sticker(account: dict, to_phone: str) -> bool:
    client = _make_client(account)
    try:
        await client.start()
        sets = await _safe_flood(client.get_sticker_set("HotCherry"))
        if sets and sets.stickers:
            sticker = random.choice(sets.stickers[:20])
            await asyncio.sleep(random.uniform(2, 6))
            await client.send_sticker(to_phone, sticker.file_id)
            await db.log_action(account["id"], "send_sticker", f"Стикер → {to_phone}", 2)
            return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] send_sticker: {ex}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────────────────
# Trusted account actions
# ─────────────────────────────────────────────────────────

_TRUST_OPENERS = [
    "Привет! Как ты?",
    "Эй, ты тут? 😄",
    "Привет, давно не общались!",
    "Привет! Что нового?",
    "Yo, как дела?",
    "Здарова! Что делаешь?",
    "Привет, всё норм?",
    "Хей! Как жизнь?",
]

_TRUST_FOLLOW_UPS = [
    "Ладно, напишу позже 😅",
    "Окей, пиши если что!",
    "Понял, бывает. Увидимся!",
    "Хорошо, отдыхай тогда 😊",
    "Ок, пока!",
    "Понял. Звони если что!",
]


async def do_trusted_write_to(trusted: dict, target: dict) -> bool:
    """Trusted account initiates a conversation with the target account."""
    client = _make_client(trusted)
    try:
        await client.start()
        opener = random.choice(_TRUST_OPENERS)
        await asyncio.sleep(random.uniform(3, 10))
        await _safe_flood(client.send_message(target["phone"], opener))
        await db.log_action(trusted["id"], "dm_sent", f"→ {target['phone']}: {opener}", 2)

        # Schedule a delayed response from the target account
        delay = random.randint(300, 1800)  # 5 min – 30 min
        reply = random.choice(REPLY_MESSAGES)
        await db.add_pending_response(
            account_id=target["id"],
            to_user_id=0,          # resolved when responding
            message=reply,
            delay_seconds=delay,
        )

        await asyncio.sleep(random.uniform(30, 120))
        follow_up = random.choice(_TRUST_FOLLOW_UPS)
        await _safe_flood(client.send_message(target["phone"], follow_up))
        return True
    except (UserPrivacyRestricted, UserNotMutualContact, PeerFlood) as ex:
        logger.warning(f"[{trusted['phone']}] trusted write to {target['phone']}: {ex}")
        return False
    except Exception as ex:
        logger.error(f"[{trusted['phone']}] trusted write: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


async def do_all_trusted_writes(trusted_accounts: list[dict], warming_accounts: list[dict]) -> int:
    """Each trusted account writes to a subset of warming accounts, rotated."""
    if not trusted_accounts or not warming_accounts:
        return 0
    count   = 0
    targets = warming_accounts[:]
    random.shuffle(targets)

    for i, target in enumerate(targets):
        # Rotate through trusted accounts
        trusted = trusted_accounts[i % len(trusted_accounts)]
        ok = await do_trusted_write_to(trusted, target)
        if ok:
            count += 1
        await asyncio.sleep(random.uniform(60, 300))  # delay between writes

    return count


# ─────────────────────────────────────────────────────────
# Respond to pending messages
# ─────────────────────────────────────────────────────────

async def do_pending_responses(account: dict, trusted_accounts: list[dict]) -> int:
    due = await db.get_due_responses(account["id"])
    if not due:
        return 0

    client = _make_client(account)
    count  = 0
    try:
        await client.start()
        for row in due:
            # Find who to reply to — last message from a trusted account
            target_phone = None
            for ta in trusted_accounts:
                target_phone = ta["phone"]
                break
            if not target_phone:
                break

            try:
                await asyncio.sleep(random.uniform(5, 20))
                await client.send_message(target_phone, row["message"])
                await db.mark_response_done(row["id"])
                await db.log_action(account["id"], "received_message", f"Ответил: {row['message'][:40]}", 1)
                count += 1
            except Exception as ex:
                logger.warning(f"[{account['phone']}] pending_response: {ex}")
                await db.mark_response_done(row["id"])
    except Exception as ex:
        logger.error(f"[{account['phone']}] pending_responses: {ex}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return count


# ─────────────────────────────────────────────────────────
# Status / human messages
# ─────────────────────────────────────────────────────────

async def do_send_status_message(
    account: dict,
    target_phones: list[str],
    msg_type: str = "sleep",
) -> bool:
    if not target_phones:
        return False
    if msg_type == "sleep":
        text = random.choice(SLEEP_MESSAGES)
    elif msg_type == "morning":
        text = random.choice(MORNING_MESSAGES)
    else:
        text = random.choice(BUSY_MESSAGES)

    target = random.choice(target_phones)
    client = _make_client(account)
    try:
        await client.start()
        await asyncio.sleep(random.uniform(2, 8))
        await _safe_flood(client.send_message(target, text))
        await db.log_action(account["id"], "status_msg", f"{msg_type}: {text[:40]}", 1)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] status_msg: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Import contact
# ─────────────────────────────────────────────────────────

async def do_add_contact(account: dict, target: dict) -> bool:
    from pyrogram.types import InputPhoneContact
    client = _make_client(account)
    rng    = random.Random(account["id"] + target["id"])
    label  = _random_name(rng.randint(0, 9999))
    try:
        await client.start()
        contact = InputPhoneContact(
            phone=target["phone"],
            first_name=label.split(" ")[0] if " " in label else label,
            last_name=" ".join(label.split(" ")[1:]) if " " in label else "",
        )
        await _safe_flood(client.import_contacts([contact]))
        await db.log_action(account["id"], "add_contact", f"Добавил {target['phone']} как «{label}»", 1)
        return True
    except Exception as ex:
        logger.error(f"[{account['phone']}] add_contact: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# Send first message batch (unlocked after warmup)
# ─────────────────────────────────────────────────────────

_FIRST_MESSAGES = [
    "Привет! Как ты?",
    "Привет, ты тут? 🙂",
    "Эй, можем поговорить?",
    "Привет! Откуда ты?",
    "Здравствуй! Как дела?",
    "Привет, нашёл тебя тут 😄",
]


async def do_first_write_batch(account: dict, targets: list[dict], count: int = 8) -> int:
    client = _make_client(account)
    sent   = 0
    try:
        await client.start()
        random.shuffle(targets)
        for t in targets[:count]:
            try:
                opener = random.choice(_FIRST_MESSAGES)
                await asyncio.sleep(random.uniform(30, 120))
                await _safe_flood(client.send_message(t["phone"], opener))
                await db.log_action(account["id"], "first_write", f"→ {t['phone']}: {opener}", 3)
                sent += 1
            except Exception as ex:
                logger.warning(f"[{account['phone']}] first_write → {t['phone']}: {ex}")
    except Exception as ex:
        logger.error(f"[{account['phone']}] first_write_batch: {ex}")
    finally:
        try:
            await client.stop()
        except Exception:
            pass
    return sent


# ─────────────────────────────────────────────────────────
# Main warming step
# ─────────────────────────────────────────────────────────

async def step_account(
    account: dict,
    trusted_accounts: list[dict],
    all_warming: list[dict],
    hour: int,
) -> int:
    """
    Run one warming step for an account.
    Returns score delta earned.
    """
    acc_id = account["id"]
    day    = account.get("day", 0)
    score  = account.get("score", 0)
    wd     = account.get("warmup_days") or 10

    if _is_in_hold(account):
        logger.info(f"[{account['phone']}] in hold, skipping")
        return 0

    peers = [a for a in all_warming if a["id"] != acc_id and a.get("has_session")]
    peer_phones = [p["phone"] for p in peers]

    earned = 0

    # ── Pending responses ─────────────────────────────────
    if trusted_accounts:
        resp = await do_pending_responses(account, trusted_accounts)
        earned += resp

    # ── Status messages (night / morning) ─────────────────
    if peer_phones:
        if 0 <= hour <= 2 and random.random() < 0.15:
            await do_send_status_message(account, peer_phones, "sleep")
            earned += 1
        elif 6 <= hour <= 8 and random.random() < 0.20:
            await do_send_status_message(account, peer_phones, "morning")
            earned += 1
        elif 12 <= hour <= 13 and random.random() < 0.10:
            await do_send_status_message(account, peer_phones, "busy")
            earned += 1

    # ── Day-based actions ──────────────────────────────────
    intensity = min(1.0, day / max(wd, 1))

    # Day 1+: update profile
    if day == 0:
        await do_update_profile(account)
        earned += 3
        await db.update_account(acc_id, day=1, profile_changed=1)

    # Days 1+: join channels (2-3 per day early on)
    if random.random() < 0.6 + intensity * 0.2:
        ch_count = random.randint(1, 3 if day < 5 else 2)
        joined = await do_join_channel(account, count=ch_count, with_delay=True)
        earned += joined * 3

    # Days 1+: SpamBot (occasionally)
    if random.random() < 0.25:
        await do_spambot(account)
        earned += 2

    # Days 1+: visit random bot
    if random.random() < 0.30:
        await do_visit_random_bot(account)
        earned += 1

    # Days 2+: create personal channel (up to 2)
    if day >= 2:
        own = db.get_own_channels(account)
        if len(own) < 2 and random.random() < 0.35:
            await do_create_own_channel(account)
            earned += 5

    # Days 2+: post to personal channel
    if day >= 2:
        own = db.get_own_channels(account)
        if own and random.random() < 0.50:
            await do_post_to_own_channel(account)
            earned += 2

    # Days 2+: forward posts
    if day >= 2 and peers and random.random() < 0.25:
        await do_forward_channel_post(account, peers)
        earned += 2

    # Days 3+: create group chat (random subset 2-8 members)
    if day >= 3 and peers and random.random() < 0.20:
        size    = random.randint(2, min(8, len(peers)))
        members = random.sample(peers, size)
        chat_id = await do_create_group(account, members)
        if chat_id:
            earned += 4

    # Days 3+: send group message if groups exist
    if day >= 3 and random.random() < 0.40:
        groups = await db.get_all_groups()
        if groups:
            chosen = random.choice(groups)
            await do_group_message(account, chosen["chat_id"])
            earned += 2

    # Days 5+: add contacts with random names
    if day >= 5 and peers and random.random() < 0.25:
        target = random.choice(peers)
        await do_add_contact(account, target)
        earned += 1

    # Days wd+: first writes batch (warmup finished)
    if day >= wd and random.random() < 0.30:
        outsiders = [a for a in all_warming if a["id"] != acc_id]
        if outsiders:
            n = await do_first_write_batch(account, outsiders, count=random.randint(3, 8))
            earned += n * 3

    # Advance day counter (each full run = +1 day)
    new_day = day + 1
    new_score = min(100, score + earned)
    await db.update_account(acc_id, day=new_day, score=new_score, last_active=datetime.now().isoformat())

    logger.info(f"[{account['phone']}] step done: day {day}→{new_day}, score +{earned}")
    return earned
