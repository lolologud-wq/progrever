"""
Manual 2 — Independent warming (imitate a live human).

Actions performed gradually over ~2 weeks:
  - Join 5-10 channels/chats per day
  - Write /start to @SpamBot and press buttons
  - Write to @BotFather (create/list bots)
  - Send 'привет' to public groups
  - Create group chats with other warming accounts
  - Add other accounts to group chats
  - Exchange messages in groups and DMs
  - Send stickers and photos
  - Set/change/remove avatars
  - Set cloud password (if phishing account)
  - Gradually increase activity each day
"""

from __future__ import annotations

import asyncio
import random
import os
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import (
    FloodWait, PeerFlood, UserBannedInChannel,
    ChannelPrivate, ChatAdminRequired, UserNotParticipant
)
from pyrogram.types import InputPhoneContact

import database as db
from config import (
    SESSIONS_DIR, API_ID, API_HASH,
    M2_CHANNELS_PER_DAY, DELAY_MIN, DELAY_MAX,
    WARM_CHANNELS, SPAM_BOT, BOT_FATHER
)

# Extended public channels/chats (safe for joining)
EXTRA_CHANNELS = [
    "durov", "telegram", "TelegramTips", "telegramwallpapers",
    "cryptonewsru", "bbcrussian", "rian_ru", "meduzaio",
    "rt_russian", "sport24live",
]

GROUP_NAMES = [
    "Огородники 🌱", "Одногруппники", "Друзья 😊",
    "Др. 07.25 🎂", "Наш чатик 💬", "Семейный чат",
    "Коллеги", "Старая гвардия", "Путешественники ✈️",
    "Книжный клуб 📚", "Спортивная команда 🏃",
]

GROUP_MESSAGES = [
    "Привет всем 👋", "Как дела?", "Хорошего дня! ☀️",
    "Что нового?", "Всем привет 😄", "Я здесь 🙂",
    "Хей 👋", "Добрый день!", "Привет, народ!",
    "Отличный день сегодня!", "Как погода у вас?",
    "Выходные скоро, ура! 🎉", "Что планируете?",
]

DM_MESSAGES = [
    "Привет! Как дела?", "Хей 👋 Что нового?",
    "Всё ок?", "Давно не общались!", "Привет 😊",
]

SPAMBOT_MESSAGES = ["/start"]
BOTFATHER_MESSAGES = ["/start", "/mybots"]

AVATAR_DIR = "media"


def _make_client(session_name: str) -> Client:
    return Client(
        name=f"{SESSIONS_DIR}/{session_name}",
        api_id=API_ID,
        api_hash=API_HASH,
        no_updates=True,
    )


async def _safe_delay(min_s: int = None, max_s: int = None):
    lo = min_s or DELAY_MIN
    hi = max_s or DELAY_MAX
    await asyncio.sleep(random.randint(lo, hi))


async def join_channels(account: dict, count: int = None) -> int:
    """Join random public channels (5-10 per day)."""
    n = count or random.randint(5, M2_CHANNELS_PER_DAY)
    channels = random.sample(EXTRA_CHANNELS, min(n, len(EXTRA_CHANNELS)))
    session = account.get("session_file") or account["phone"]
    joined = 0
    try:
        async with _make_client(session) as client:
            for ch in channels:
                try:
                    await client.join_chat(ch)
                    joined += 1
                    await db.log_action(account["id"], "join_channel", f"@{ch}", 2)
                    await _safe_delay(30, 120)
                except (ChannelPrivate, UserBannedInChannel):
                    pass
                except FloodWait as e:
                    await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"join_channels: {ex}")
    return joined


async def write_to_spambot(account: dict) -> bool:
    """Write /start to SpamBot and interact."""
    session = account.get("session_file") or account["phone"]
    try:
        async with _make_client(session) as client:
            await client.send_message(SPAM_BOT, "/start")
            await _safe_delay(15, 40)
            # Try pressing inline buttons by sending another message
            async for msg in client.get_chat_history(SPAM_BOT, limit=3):
                if msg.reply_markup:
                    # Click first available button
                    try:
                        btn = msg.reply_markup.inline_keyboard[0][0]
                        await msg.click(btn.callback_data)
                    except Exception:
                        pass
                    break
            await db.log_action(account["id"], "spambot", "/start + button click", 4)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"spambot: {ex}")
    return False


async def write_to_botfather(account: dict) -> bool:
    """Visit @BotFather and send /mybots."""
    session = account.get("session_file") or account["phone"]
    try:
        async with _make_client(session) as client:
            await client.send_message(BOT_FATHER, "/start")
            await _safe_delay(10, 30)
            await client.send_message(BOT_FATHER, "/mybots")
            await db.log_action(account["id"], "botfather", "/start /mybots", 3)
        return True
    except Exception as ex:
        await db.log_action(account["id"], "error", f"botfather: {ex}")
    return False


async def send_group_message(account: dict, chat_id: int) -> bool:
    """Send a random message to a group chat."""
    session = account.get("session_file") or account["phone"]
    msg = random.choice(GROUP_MESSAGES)
    try:
        async with _make_client(session) as client:
            await client.send_message(chat_id, msg)
            await db.log_action(account["id"], "group_msg", f"→ chat {chat_id}: {msg}", 3)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"group_msg: {ex}")
    return False


async def send_dm(account: dict, target: dict) -> bool:
    """Send a DM to another warming account."""
    session = account.get("session_file") or account["phone"]
    msg = random.choice(DM_MESSAGES)
    try:
        async with _make_client(session) as client:
            contact = await client.import_contacts([
                InputPhoneContact(
                    phone=target["phone"],
                    first_name=target.get("first_name") or "Друг",
                    last_name=target.get("last_name") or "",
                )
            ])
            if not contact.users:
                return False
            user = contact.users[0]
            await client.send_message(user.id, msg)
            await db.log_action(account["id"], "dm_sent", f"→ {target['phone']}: {msg}", 3)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"dm: {ex}")
    return False


async def create_group_chat(creator: dict, members: list[dict]) -> int | None:
    """Create a group chat and add members."""
    session = creator.get("session_file") or creator["phone"]
    title = random.choice(GROUP_NAMES)
    try:
        async with _make_client(session) as client:
            # Import contacts first
            phone_contacts = []
            for m in members:
                phone_contacts.append(InputPhoneContact(
                    phone=m["phone"],
                    first_name=m.get("first_name") or "Друг",
                    last_name=m.get("last_name") or "",
                ))
            if phone_contacts:
                imported = await client.import_contacts(phone_contacts)
                user_ids = [u.id for u in imported.users]
            else:
                user_ids = []

            if not user_ids:
                return None

            chat = await client.create_group(title, user_ids[:9])
            chat_id = chat.id
            await db.log_action(creator["id"], "create_group",
                                 f"'{title}' id={chat_id}", 6)

            # Welcome message
            await asyncio.sleep(5)
            await client.send_message(chat_id, "Привет всем в этом чате! 👋")

            group_db_id = await db.add_group(chat_id, title, creator["id"])
            await db.add_group_member(group_db_id, creator["id"])
            for m in members:
                await db.add_group_member(group_db_id, m["id"])

            return chat_id
    except FloodWait as e:
        await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(creator["id"], "error", f"create_group: {ex}")
    return None


async def send_sticker(account: dict, chat_id: int | str) -> bool:
    """Send a random sticker from a known set."""
    session = account.get("session_file") or account["phone"]
    packs = ["HotCherry", "Animals", "Meow"]
    try:
        async with _make_client(session) as client:
            for pack in packs:
                try:
                    sticker_set = await client.get_sticker_set(pack)
                    sticker = random.choice(sticker_set.stickers[:15])
                    await client.send_sticker(chat_id, sticker.file_id)
                    await db.log_action(account["id"], "send_sticker",
                                         f"→ {chat_id}", 2)
                    return True
                except Exception:
                    continue
    except Exception as ex:
        await db.log_action(account["id"], "error", f"sticker: {ex}")
    return False


async def set_avatar(account: dict, photo_path: str) -> bool:
    """Set account profile photo."""
    session = account.get("session_file") or account["phone"]
    if not os.path.exists(photo_path):
        return False
    try:
        async with _make_client(session) as client:
            await client.set_profile_photo(photo=photo_path)
            await db.update_account(account["id"], avatar_set=1)
            await db.log_action(account["id"], "set_avatar", photo_path, 3)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value + 5)
    except Exception as ex:
        await db.log_action(account["id"], "error", f"set_avatar: {ex}")
    return False


async def remove_avatar(account: dict) -> bool:
    """Remove current profile photo."""
    session = account.get("session_file") or account["phone"]
    try:
        async with _make_client(session) as client:
            photos = await client.get_profile_photos("me")
            async for photo in photos:
                await client.delete_profile_photos(photo.file_id)
                break
            await db.update_account(account["id"], avatar_set=0)
            await db.log_action(account["id"], "remove_avatar", "", 1)
        return True
    except Exception as ex:
        await db.log_action(account["id"], "error", f"remove_avatar: {ex}")
    return False


async def set_cloud_password(account: dict, password: str = None) -> bool:
    """Enable cloud (two-step verification) password."""
    session = account.get("session_file") or account["phone"]
    pwd = password or f"Secure{random.randint(1000,9999)}!"
    try:
        async with _make_client(session) as client:
            await client.enable_cloud_password(pwd)
            await db.log_action(account["id"], "cloud_password", "2FA enabled", 5)
        return True
    except Exception as ex:
        await db.log_action(account["id"], "error", f"cloud_pwd: {ex}")
    return False


async def step_account_manual2(account: dict, peer_accounts: list[dict]) -> bool:
    """Execute a daily warming cycle for Manual 2."""
    acc_id = account["id"]
    day = account.get("day", 0)
    score = account.get("score", 0)

    # Gradually increase intensity based on day
    intensity = min(1.0, (day + 1) / 14.0)

    actions_done = 0

    # 1. Join channels (5-10/day)
    join_count = int(5 + intensity * 5)
    joined = await join_channels(account, join_count)
    actions_done += joined
    await _safe_delay(60, 180)

    # 2. SpamBot interaction (every 2 days)
    if day % 2 == 0:
        await write_to_spambot(account)
        actions_done += 1
        await _safe_delay()

    # 3. BotFather (every 3 days)
    if day % 3 == 0:
        await write_to_botfather(account)
        actions_done += 1
        await _safe_delay()

    # 4. DMs to other warming accounts (1-3 per day)
    dm_count = max(1, int(intensity * 3))
    peers = [p for p in peer_accounts if p["id"] != acc_id and p.get("has_session")]
    random.shuffle(peers)
    for peer in peers[:dm_count]:
        await send_dm(account, peer)
        await _safe_delay()

    # 5. Group chat interactions
    groups = await db.get_all_groups()
    if groups:
        group = random.choice(groups)
        chat_id = group.get("chat_id")
        if chat_id:
            await send_group_message(account, chat_id)
            await _safe_delay(20, 60)
            # Sometimes send a sticker in group
            if random.random() < 0.4:
                await send_sticker(account, chat_id)
                await _safe_delay(15, 45)
    else:
        # Create a group if enough peers
        if len(peers) >= 2 and day >= 3:
            members = random.sample(peers, min(3, len(peers)))
            await create_group_chat(account, members)

    # 6. Avatar rotation (set on day 5, remove on day 10)
    if day == 5:
        avatars = [f for f in os.listdir(AVATAR_DIR)
                   if f.endswith((".jpg", ".png", ".jpeg"))] if os.path.exists(AVATAR_DIR) else []
        if avatars:
            photo = os.path.join(AVATAR_DIR, random.choice(avatars))
            await set_avatar(account, photo)

    if day == 10 and account.get("avatar_set"):
        if random.random() < 0.5:
            await remove_avatar(account)

    # Advance day & score
    await db.update_account(acc_id, day=day + 1, last_active=datetime.now().isoformat())

    return actions_done > 0
