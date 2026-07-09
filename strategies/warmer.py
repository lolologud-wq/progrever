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
    PEER_DM_OPENERS, PEER_CHAT_REPLIES, GROUP_CHAT_REPLIES,
    DELAY_MIN, DELAY_MAX, HOUR_ACTIVITY,
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


def _get_peers(account: dict, all_accounts: list[dict]) -> list[dict]:
    """All other accounts with an active session."""
    return [
        a for a in all_accounts
        if a["id"] != account["id"] and a.get("has_session")
    ]


async def _schedule_peer_reply(
    receiver: dict,
    sender_phone: str,
    message: str,
    delay_seconds: int,
    response_type: str = "dm",
    chat_id: int = None,
):
    await db.add_pending_response(
        account_id=receiver["id"],
        message=message,
        delay_seconds=delay_seconds,
        target_phone=sender_phone,
        chat_id=chat_id,
        response_type=response_type,
    )


async def do_dm_to_peer(
    account: dict,
    target: dict,
    use_sticker: bool = False,
    schedule_reply: bool = True,
) -> bool:
    """Send a DM (or sticker) from one account to another."""
    client = _make_client(account)
    try:
        await client.start()

        if use_sticker and random.random() < 0.5:
            sets = await _safe_flood(client.get_sticker_set("HotCherry"))
            if sets and sets.stickers:
                sticker = random.choice(sets.stickers[:20])
                await asyncio.sleep(random.uniform(2, 8))
                await client.send_sticker(target["phone"], sticker.file_id)
                await db.log_action(
                    account["id"], "dm_sticker",
                    f"Стикер → {target['phone']}", 2,
                )
            else:
                use_sticker = False

        if not use_sticker:
            text = random.choice(PEER_DM_OPENERS + PEER_CHAT_REPLIES)
            await asyncio.sleep(random.uniform(3, 15))
            await _safe_flood(client.send_message(target["phone"], text))
            await db.log_action(
                account["id"], "dm_peer",
                f"→ {target['phone']}: {text[:40]}", 2,
            )

        if schedule_reply and random.random() < 0.55:
            reply = random.choice(PEER_CHAT_REPLIES)
            delay = random.randint(120, 1200)
            await _schedule_peer_reply(target, account["phone"], reply, delay)

        return True
    except (UserPrivacyRestricted, UserNotMutualContact, PeerFlood) as ex:
        logger.warning(f"[{account['phone']}] dm_peer → {target['phone']}: {ex}")
        if random.random() < 0.6:
            await do_add_contact(account, target)
        return False
    except Exception as ex:
        logger.error(f"[{account['phone']}] dm_peer: {ex}")
        return False
    finally:
        try:
            await client.stop()
        except Exception:
            pass


async def do_group_chat_round(account: dict, all_accounts: list[dict]) -> bool:
    """Post in a group and schedule another account to reply."""
    groups = await db.get_all_groups()
    if not groups:
        peers = _get_peers(account, all_accounts)
        if len(peers) >= 2:
            members = random.sample(peers, min(random.randint(2, 4), len(peers)))
            chat_id = await do_create_group(account, members)
            if chat_id:
                groups = await db.get_all_groups()
        if not groups:
            return False

    group   = random.choice(groups)
    chat_id = group["chat_id"]
    ok      = await do_group_message(account, chat_id)
    if not ok:
        return False

    peers = [p for p in _get_peers(account, all_accounts) if p["id"] != account["id"]]
    if peers:
        replier = random.choice(peers)
        reply   = random.choice(GROUP_CHAT_REPLIES + _GROUP_MESSAGES)
        delay   = random.randint(90, 900)
        await _schedule_peer_reply(
            replier, account["phone"], reply, delay,
            response_type="group", chat_id=chat_id,
        )
        if random.random() < 0.35:
            second = random.choice([p for p in peers if p["id"] != replier["id"]] or peers)
            await _schedule_peer_reply(
                second, account["phone"],
                random.choice(GROUP_CHAT_REPLIES), random.randint(300, 1500),
                response_type="group", chat_id=chat_id,
            )
    return True


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
            message=reply,
            delay_seconds=delay,
            target_phone=trusted["phone"],
            response_type="dm",
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

async def do_pending_responses(account: dict) -> int:
    due = await db.get_due_responses(account["id"])
    if not due:
        return 0

    client = _make_client(account)
    count  = 0
    try:
        await client.start()
        for row in due:
            target_phone = row.get("target_phone")
            chat_id      = row.get("chat_id")
            resp_type    = row.get("response_type") or "dm"

            try:
                await asyncio.sleep(random.uniform(5, 25))
                if resp_type == "group" and chat_id:
                    await _safe_flood(client.send_message(chat_id, row["message"]))
                    detail = f"В группу: {row['message'][:40]}"
                    action = "group_reply"
                elif target_phone:
                    await _safe_flood(client.send_message(target_phone, row["message"]))
                    detail = f"→ {target_phone}: {row['message'][:40]}"
                    action = "dm_reply"
                else:
                    await db.mark_response_done(row["id"])
                    continue

                await db.mark_response_done(row["id"])
                await db.log_action(account["id"], action, detail, 2)
                count += 1

                # Sometimes continue the conversation
                if target_phone and resp_type == "dm" and random.random() < 0.3:
                    follow = random.choice(PEER_CHAT_REPLIES)
                    delay  = random.randint(300, 1800)
                    sender_acc = await db.get_account_by_phone(target_phone)
                    if sender_acc:
                        await _schedule_peer_reply(
                            sender_acc, account["phone"], follow, delay,
                        )
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
        resp = await do_pending_responses(account)
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


# ─────────────────────────────────────────────────────────
# Per-account scheduler (next action planning & execution)
# ─────────────────────────────────────────────────────────

ACTION_LABELS = {
    "hold_wait":        "⏳ Ожидание окончания холда",
    "profile_change":   "📝 Обновить профиль",
    "join_channel":     "📢 Вступить в каналы",
    "spambot":          "🤖 Написать в SpamBot",
    "bot_visit":        "🤖 Посетить бота",
    "create_channel":   "📺 Создать личный канал",
    "channel_post":     "📬 Пост в личный канал",
    "forward_post":     "↗️ Переслать пост из канала",
    "create_group":     "🏠 Создать группу",
    "group_msg":        "💬 Сообщение в группу",
    "group_chat":       "💬 Переписка в группе",
    "dm_peer":          "💬 Написать в ЛС",
    "dm_sticker":       "🎭 Стикер в ЛС",
    "dm_reply":         "💬 Ответить в ЛС",
    "group_reply":      "💬 Ответить в группе",
    "add_contact":      "👤 Добавить контакт",
    "pending_response": "💬 Ответить на сообщение",
    "status_sleep":     "🌙 Статус: иду спать",
    "status_morning":   "☀️ Статус: доброе утро",
    "status_busy":      "💼 Статус: занят",
    "first_write":      "✉️ Первое сообщение",
    "trusted_write":    "🟣 Написать прогреваемому",
    "idle":             "💤 Авто-прогрев выключен",
    "paused":           "⏸ Авто-прогрев на паузе",
}


def format_next_action_line(acc: dict) -> str:
    if not acc.get("has_session"):
        return "🔮 Следующее: нет сессии"
    if not acc.get("auto_warming", 1):
        return "🔮 Следующее: авто-прогрев выключен"
    if acc.get("warmup_complete") and not acc.get("is_trusted"):
        return "🔮 Следующее: прогрев завершён"

    action = acc.get("next_action") or "idle"
    label  = ACTION_LABELS.get(action, action)
    at     = acc.get("next_action_at")

    if not at:
        return f"🔮 Следующее: {label}\n⏰ планируется..."

    try:
        dt  = datetime.fromisoformat(at)
        rem = dt - datetime.now()
        if rem.total_seconds() <= 0:
            eta = "сейчас / скоро"
        else:
            total = int(rem.total_seconds())
            h, m  = total // 3600, (total % 3600) // 60
            s     = total % 60
            if h:
                eta = f"через {h}ч {m}м"
            elif m:
                eta = f"через {m}м {s}с"
            else:
                eta = f"через {s}с"
        clock = dt.strftime("%d.%m %H:%M")
        return f"🔮 Следующее: <b>{label}</b>\n⏰ {eta} (в {clock})"
    except Exception:
        return f"🔮 Следующее: {label}"


def _calc_next_delay_seconds(account_id: int, hour: int) -> int:
    activity = HOUR_ACTIVITY.get(hour, 0.3)
    if activity >= 0.6:
        lo, hi = 18, 45
    elif activity >= 0.3:
        lo, hi = 25, 60
    else:
        lo, hi = 40, 90
    rng = random.Random(account_id + int(datetime.now().timestamp() // 1200))
    return rng.randint(lo * 60, hi * 60)


async def _pick_next_action(account: dict, all_accounts: list[dict], hour: int) -> str:
    if account.get("is_trusted"):
        return "trusted_write"
    if _is_in_hold(account):
        return "hold_wait"
    if account.get("warmup_complete"):
        return "idle"
    if not account.get("auto_warming", 1):
        return "paused"

    due = await db.get_due_responses(account["id"])
    if due:
        return "pending_response"

    day    = account.get("day", 0)
    wd     = account.get("warmup_days") or 10
    acc_id = account["id"]
    peers  = _get_peers(account, all_accounts)

    if 0 <= hour <= 2 and random.random() < 0.12:
        return "status_sleep"
    if 6 <= hour <= 8 and random.random() < 0.15:
        return "status_morning"
    if 12 <= hour <= 13 and random.random() < 0.10:
        return "status_busy"

    candidates: list[tuple[str, int]] = []

    # Chat between accounts — high priority from day 1
    if peers:
        candidates.append(("dm_peer", 14))
        candidates.append(("dm_sticker", 8))
        candidates.append(("group_chat", 12))
        candidates.append(("add_contact", 5))

    if day == 0:
        candidates.append(("profile_change", 10))
    candidates += [
        ("join_channel", 7),
        ("spambot", 4),
        ("bot_visit", 4),
    ]
    if day >= 2:
        own = db.get_own_channels(account)
        if len(own) < 2:
            candidates.append(("create_channel", 6))
        if own:
            candidates.append(("channel_post", 6))
        if peers:
            candidates.append(("forward_post", 5))
    if day >= 1 and peers:
        candidates.append(("create_group", 5))
    if day >= wd and peers:
        candidates.append(("first_write", 5))

    if not candidates:
        return "dm_peer" if peers else "join_channel"

    total = sum(w for _, w in candidates)
    rng   = random.Random(acc_id + day * 997 + int(datetime.now().strftime("%Y%m%d")))
    roll  = rng.randint(1, total)
    acc_w = 0
    for name, weight in candidates:
        acc_w += weight
        if roll <= acc_w:
            return name
    return candidates[-1][0]


async def plan_next_action(account_id: int) -> None:
    account = await db.get_account(account_id)
    if not account or not account.get("has_session"):
        return

    if not account.get("auto_warming", 1):
        await db.update_account(
            account_id,
            next_action="paused",
            next_action_at=None,
        )
        return

    all_accounts = await db.get_all_accounts()
    hour   = datetime.now().hour
    action = await _pick_next_action(account, all_accounts, hour)

    if action == "hold_wait":
        at = account.get("hold_until") or datetime.now().isoformat()
    elif action in ("idle", "paused"):
        at = None
    else:
        delay = _calc_next_delay_seconds(account_id, hour)
        at = (datetime.now() + timedelta(seconds=delay)).isoformat()

    await db.update_account(
        account_id,
        next_action=action,
        next_action_at=at,
    )
    logger.info(f"[{account['phone']}] planned: {action} at {at}")


async def _advance_progress(account_id: int, account: dict, score_delta: int) -> None:
    if score_delta:
        await db.add_score(account_id, score_delta)

    step_count = (account.get("warm_step_count") or 0) + 1
    updates: dict = {
        "warm_step_count": step_count,
        "last_active": datetime.now().isoformat(),
    }
    if step_count >= 4 and not account.get("is_trusted"):
        day = account.get("day", 0)
        wd  = account.get("warmup_days") or 10
        if day < wd:
            updates["day"] = day + 1
        updates["warm_step_count"] = 0
    await db.update_account(account_id, **updates)


async def execute_planned_action(
    account: dict,
    action: str,
    trusted_accounts: list[dict],
    all_accounts: list[dict],
) -> tuple[bool, str]:
    """Run one scheduled action. Returns (success, user-facing message)."""
    acc_id  = account["id"]
    peers   = _get_peers(account, all_accounts)
    phones  = [p["phone"] for p in peers]
    warming = [a for a in all_accounts if not a.get("is_trusted") and a.get("has_session")]

    if action == "hold_wait":
        return False, "Ожидание холда"

    if action == "profile_change":
        ok = await do_update_profile(account)
        return ok, "Профиль обновлён" if ok else "Ошибка профиля"

    if action == "join_channel":
        n = await do_join_channel(account, count=random.randint(1, 2), with_delay=True)
        return n > 0, f"Вступил в {n} канал(ов)"

    if action == "spambot":
        ok = await do_spambot(account)
        return ok, "SpamBot" if ok else "Ошибка SpamBot"

    if action == "bot_visit":
        ok = await do_visit_random_bot(account)
        return ok, "Посещение бота" if ok else "Ошибка бота"

    if action == "create_channel":
        ch = await do_create_own_channel(account)
        return ch is not None, "Канал создан" if ch else "Не удалось создать канал"

    if action == "channel_post":
        ok = await do_post_to_own_channel(account)
        return ok, "Пост в канал" if ok else "Нет канала для поста"

    if action == "forward_post" and peers:
        ok = await do_forward_channel_post(account, peers)
        return ok, "Переслан пост" if ok else "Не удалось переслать"

    if action == "create_group" and peers:
        size    = random.randint(2, min(6, len(peers)))
        members = random.sample(peers, size)
        ch      = await do_create_group(account, members)
        return ch is not None, "Группа создана" if ch else "Не удалось создать группу"

    if action == "dm_peer" and peers:
        target = random.choice(peers)
        ok = await do_dm_to_peer(account, target, use_sticker=False)
        return ok, f"ЛС → {target['phone']}" if ok else "Ошибка ЛС"

    if action == "dm_sticker" and peers:
        target = random.choice(peers)
        ok = await do_dm_to_peer(account, target, use_sticker=True)
        return ok, f"Стикер → {target['phone']}" if ok else "Ошибка стикера"

    if action == "group_chat":
        ok = await do_group_chat_round(account, all_accounts)
        return ok, "Переписка в группе" if ok else "Нет групп для чата"

    if action == "group_msg":
        groups = await db.get_all_groups()
        if groups:
            ok = await do_group_message(account, random.choice(groups)["chat_id"])
            return ok, "Сообщение в группу" if ok else "Ошибка группы"
        return False, "Нет групп"

    if action == "add_contact" and peers:
        ok = await do_add_contact(account, random.choice(peers))
        return ok, "Контакт добавлен" if ok else "Ошибка контакта"

    if action == "pending_response":
        n = await do_pending_responses(account)
        return n > 0, f"Отправлено ответов: {n}"

    if action == "status_sleep" and phones:
        ok = await do_send_status_message(account, phones, "sleep")
        return ok, "Статус: сон" if ok else "Ошибка статуса"

    if action == "status_morning" and phones:
        ok = await do_send_status_message(account, phones, "morning")
        return ok, "Статус: утро" if ok else "Ошибка статуса"

    if action == "status_busy" and phones:
        ok = await do_send_status_message(account, phones, "busy")
        return ok, "Статус: занят" if ok else "Ошибка статуса"

    if action == "first_write" and peers:
        n = await do_first_write_batch(account, peers, count=random.randint(1, 3))
        return n > 0, f"Первых сообщений: {n}"

    if action == "trusted_write":
        targets = [a for a in all_accounts if not a.get("is_trusted") and a.get("has_session")]
        if not targets:
            return False, "Нет целей для трастового"
        target = random.choice(targets)
        ok = await do_trusted_write_to(account, target)
        return ok, f"Написал {target['phone']}" if ok else "Ошибка трастового"

    return False, "Действие пропущено"


ACTION_SCORES = {
    "profile_change": 3,
    "join_channel": 3,
    "spambot": 2,
    "bot_visit": 1,
    "create_channel": 5,
    "channel_post": 2,
    "forward_post": 2,
    "create_group": 4,
    "group_chat": 3,
    "dm_peer": 2,
    "dm_sticker": 2,
    "group_msg": 2,
    "add_contact": 1,
    "pending_response": 1,
    "status_sleep": 1,
    "status_morning": 1,
    "status_busy": 1,
    "first_write": 3,
    "trusted_write": 2,
}


async def run_account_action(account_id: int) -> str:
    """Execute the currently planned action for one account."""
    account = await db.get_account(account_id)
    if not account:
        return "❌ Аккаунт не найден"

    if not account.get("has_session"):
        return "❌ Нет сессии"

    action = account.get("next_action")
    if not action or action in ("idle", "paused", "hold_wait"):
        if _is_in_hold(account):
            return "⏳ Аккаунт на холде — дождись окончания или отключи холд"
        await plan_next_action(account_id)
        account = await db.get_account(account_id)
        action = account.get("next_action")
        if not action or action in ("idle", "paused", "hold_wait"):
            return "⚠️ Нет запланированного действия"

    trusted = await db.get_trusted_accounts()
    all_acc = await db.get_all_accounts()

    label = ACTION_LABELS.get(action, action)
    ok, detail = await execute_planned_action(account, action, trusted, all_acc)

    score = ACTION_SCORES.get(action, 1) if ok else 0
    if ok:
        await _advance_progress(account_id, account, score)
        await db.log_action(account_id, action, f"Авто: {detail}", score)

    fresh = await db.get_account(account_id)
    if fresh:
        from warming_engine import check_warmup_completion
        await check_warmup_completion(fresh)

    await plan_next_action(account_id)

    if ok:
        return f"✅ <b>{label}</b>\n{detail}"
    return f"⚠️ <b>{label}</b>\n{detail}"
