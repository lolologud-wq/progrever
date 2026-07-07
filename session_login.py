"""
Utility script: log in to a Telegram account and create a .session file.
Run this separately for each account you want to add.

Usage:
    python session_login.py +79001234567
"""

import asyncio
import sys
import os
from pyrogram import Client
from config import API_ID, API_HASH, SESSIONS_DIR


async def login(phone: str):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_name = phone.replace("+", "")
    session_path = os.path.join(SESSIONS_DIR, session_name)

    print(f"Logging in as {phone}...")
    async with Client(
        name=session_path,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=phone,
    ) as client:
        me = await client.get_me()
        print(f"✅ Logged in: {me.first_name} (@{me.username or 'no username'})")
        print(f"Session saved: {session_path}.session")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python session_login.py +79001234567")
        sys.exit(1)
    phone = sys.argv[1]
    asyncio.run(login(phone))
