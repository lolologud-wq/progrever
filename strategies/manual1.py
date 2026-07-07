"""
Manual 1 (Priority Strategy) — Trust-based warming.

Flow:
  Day 0     → Hold 24h, no actions (отлежаться)
  Day 1     → Change profile description + name (after hold)
  Day 2-5   → Trusted accounts write to this account; account joins 2 channels/day
  Day 6+    → Account can write first to up to 8 people/day
              Best to start with a sticker, then continue conversation

Trusted (🟣) accounts send messages to the warming accounts.
3-4 trusted accounts contacting the same target over 5 days.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.errors import (
    FloodWait, UserNotParticipant, ChannelPrivate,
    PeerFlood, UserBannedInChannel
)
from pyrogram.types import InputPhoneContact

import database as db
from config import (
    SESSIONS_DIR, API_ID, API_HASH,
    M1_HOLD_HOURS, M1_PROFILE_CHANGE_DAY, M1_TRUST_MESSAGES_PER_DAY,
    M1_CHANNEL_JOINS_PER_DAY, M1_WARMUP_DAYS, M1_MAX_FIRST_WRITES_PER_DAY,
    WARM_CHANNELS, DELAY_MIN, DELAY_MAX
)

BIOS = [
    "Просто живу и радуюсь 🌿",
    "Люблю путешествовать и фотографировать",
    "Читаю книги, смотрю сериалы",
    "Работаю, отдыхаю, повторяю 😄",
    "На связи не всегда, но отвечу 🙂",
    "Жизнь — это движение",
    "Люблю природу и тишину 🌲",
]

STICKERS_PACK = "HotCherry"  # popular public sticker pack fallback

FIRST_MESSAGES = [
    "Привет! 👋",
    "Хей 😊",
    "Привет, как дела?",
]

CHAT_MESSAGES = [
    "Привет всем 👋",
    "Как дела, друзья?",
    "Хорошего дня! ☀️",
    "Всем привет из чата 😄",
    "Вот и я 🙂",
]

GROUP_NAMES = [
    "Огородники 🌱",
    "Одногруппники",
    "Друзья",
    "Др. 07.25 🎂",
    "Наш чатик",
    "Семейный чат",
    "Коллеги",
    "Старая гвардия",
]


def _make_client(session_name: str) -> Client:
    return Client(
        name=f"{SESSIONS_DIR}/{session_name}",
        api_id=API_ID,
        api_hash=API_HASH,
        no_updates=True,
    )


async def _safe_delay():
    await asyncio.sleep(random.randint(DELAY_MIN, DELAY_MAX))


async def run_hold_phase(account: dict) -> bool:
    """Day 0 — just mark hold, no Telegram actions."""
    hold_until = datetime.now() + timedelta(hours=M1_HOLD_HOURS)
    await db.update_account(
        account["id"],
        hold_until=hold_until.isoformat(),
        day=0,
    )
    await db.log_action(account["id"], "hold_start",
                        f"Hold until {hold_until.strftime('%Y-%m-%d %H:%M')}", 2)
    return True


async def is_hold_over(account: dict) -> bool:
    hold_until = account.get("hold_until")
    if not hold_until:
        return True
    return datetime.now() >= datetime.fromisoformat(hold_until)


async def run_profile_change(account: dict) -> bool:
    """Day 1-2 — change bio after hold."""
    session = account.get("session_file") or account["phone"]
    try:
        async with _make_client(session) as client:
            new_bio = random.choice(BIOS)
            await client.update_profile(bio=new_bio)
            await _safe_delay()
            await db.update_account(account["id"], profile_changed=1, bio=new_bio)
            await db.log_action(account["id"], "profile_change", f"Bio: {new_bio}", 5)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 10)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"profile_change: {ex}")
    return False


async def run_channel_join(account: dict) -> bool:
    """Join 1-2 public channels per day."""
    session = account.get("session_file") or account["phone"]
    channels = random.sample(WARM_CHANNELS, min(M1_CHANNEL_JOINS_PER_DAY, len(WARM_CHANNELS)))
    joined = 0
    try:
        async with _make_client(session) as client:
            for ch in channels:
                try:
                    await client.join_chat(ch)
                    joined += 1
                    await db.log_action(account["id"], "join_channel", f"@{ch}", 3)
                    await _safe_delay()
                except (UserBannedInChannel, ChannelPrivate):
                    pass
                except FloodWait as e:
                    await asyncio.sleep(e.value + 10)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"channel_join: {ex}")
    return joined > 0


async def trusted_write_to_account(trusted: dict, target: dict) -> bool:
    """
    A trusted (🟣) account sends a message to a warming account.
    Starts with a sticker if possible, then a short text message.
    """
    t_session = trusted.get("session_file") or trusted["phone"]
    target_phone = target["phone"]

    messages = [
        "Привет! 👋",
        "Как дела?",
        "Привет, давно не виделись!",
        "Хей, что нового?",
        "Привет! Всё ок?",
    ]

    try:
        async with _make_client(t_session) as client:
            # Try to resolve target by phone
            try:
                contact = await client.import_contacts([
                    InputPhoneContact(
                        phone=target_phone,
                        first_name=target.get("first_name") or "Друг",
                        last_name=target.get("last_name") or "",
                    )
                ])
                if not contact.users:
                    return False
                user = contact.users[0]
            except Exception:
                return False

            # Send sticker first (try)
            try:
                sticker_set = await client.get_sticker_set(STICKERS_PACK)
                sticker = random.choice(sticker_set.stickers[:10])
                await client.send_sticker(user.id, sticker.file_id)
                await db.log_action(trusted["id"], "send_sticker",
                                    f"→ {target_phone}", 2)
                await asyncio.sleep(random.randint(30, 90))
            except Exception:
                pass

            # Send text
            msg = random.choice(messages)
            await client.send_message(user.id, msg)
            await db.log_action(trusted["id"], "send_message",
                                 f"→ {target_phone}: {msg}", 3)
            await db.log_action(target["id"], "received_message",
                                 f"← trusted {trusted['phone']}", 4)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 10)
    except PeerFlood:
        await db.log_action(trusted["id"], "peer_flood", "PeerFlood warning")
    except Exception as ex:
        await db.log_action(trusted["id"], "error", f"trusted_write: {ex}")
    return False


async def run_first_write(account: dict, targets: list[dict], count: int = 8) -> int:
    """
    After 5+1 days: account writes first to up to `count` people.
    Starts with a sticker, then continues.
    """
    session = account.get("session_file") or account["phone"]
    sent = 0
    try:
        async with _make_client(session) as client:
            for target in targets[:count]:
                try:
                    # Import contact
                    contact = await client.import_contacts([
                        InputPhoneContact(
                            phone=target["phone"],
                            first_name=target.get("first_name") or "Друг",
                            last_name=target.get("last_name") or "",
                        )
                    ])
                    if not contact.users:
                        continue
                    user = contact.users[0]

                    # Sticker first
                    try:
                        sticker_set = await client.get_sticker_set(STICKERS_PACK)
                        sticker = random.choice(sticker_set.stickers[:10])
                        await client.send_sticker(user.id, sticker.file_id)
                        await asyncio.sleep(random.randint(20, 60))
                    except Exception:
                        pass

                    # Text after sticker
                    msg = random.choice(FIRST_MESSAGES)
                    await client.send_message(user.id, msg)
                    await db.log_action(account["id"], "first_write",
                                         f"→ {target['phone']}: {msg}", 5)
                    sent += 1
                    await _safe_delay()
                except FloodWait as e:
                    await asyncio.sleep(e.value + 10)
                except PeerFlood:
                    await db.log_action(account["id"], "peer_flood", "Stop writing")
                    break
                except Exception as ex:
                    await db.log_action(account["id"], "error", f"first_write: {ex}")
    except Exception as ex:
        await db.log_action(account["id"], "error", f"first_write_session: {ex}")
    return sent


async def step_account_manual1(account: dict, trusted_accounts: list[dict]) -> bool:
    """Execute the next appropriate warming step for Manual 1."""
    acc_id = account["id"]
    day = account.get("day", 0)

    # Day 0: Hold phase
    if day == 0:
        if not await is_hold_over(account):
            return False  # Still holding
        # Hold is over — advance to day 1
        await db.update_account(acc_id, day=1)
        await db.log_action(acc_id, "hold_over", "24h hold complete. Starting warm.", 3)
        day = 1

    # Day 1: Change profile
    if day == 1 and not account.get("profile_changed"):
        await run_profile_change(account)
        await db.update_account(acc_id, day=2)
        return True

    # Days 2-5: Trusted accounts write + join channels
    if 2 <= day <= M1_WARMUP_DAYS:
        # Join channels
        await run_channel_join(account)

        # Trusted accounts write to this account
        donors = [t for t in trusted_accounts if t["id"] != acc_id]
        random.shuffle(donors)
        for donor in donors[:M1_TRUST_MESSAGES_PER_DAY]:
            await trusted_write_to_account(donor, account)
            await asyncio.sleep(random.randint(120, 600))

        # Advance day
        await db.update_account(acc_id, day=day + 1)
        return True

    # Day 6+: Account is ready to write first
    if day > M1_WARMUP_DAYS:
        await db.update_account(acc_id, day=day + 1)
        await db.log_action(acc_id, "ready_to_write",
                             f"Day {day}: can write first to ~{M1_MAX_FIRST_WRITES_PER_DAY}/day", 5)
        return True

    return False
