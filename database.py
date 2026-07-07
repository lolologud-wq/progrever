from __future__ import annotations

import aiosqlite
import asyncio
from datetime import datetime
from config import DB_PATH

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    session_file TEXT,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    bio TEXT,
    avatar_set INTEGER DEFAULT 0,
    strategy INTEGER DEFAULT 1,        -- 1 = Manual1, 2 = Manual2
    is_trusted INTEGER DEFAULT 0,      -- 🟣 trusted donor account
    has_session INTEGER DEFAULT 0,     -- has valid session
    score INTEGER DEFAULT 0,           -- 0..100 warming score
    day INTEGER DEFAULT 0,             -- current warming day
    hold_until TEXT,                   -- ISO datetime, hold phase
    profile_changed INTEGER DEFAULT 0, -- profile updated flag
    created_at TEXT DEFAULT (datetime('now')),
    last_active TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS warming_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    score_delta INTEGER DEFAULT 0,
    ts TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS group_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    title TEXT,
    created_by INTEGER,   -- account_id
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (created_by) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER,
    account_id INTEGER,
    joined_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, account_id),
    FOREIGN KEY (group_id) REFERENCES group_chats(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()


async def add_account(phone: str, session_file: str = None, strategy: int = 1,
                      is_trusted: bool = False) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO accounts (phone, session_file, strategy, is_trusted)
               VALUES (?, ?, ?, ?)""",
            (phone, session_file, strategy, 1 if is_trusted else 0)
        )
        await db.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = await db.execute("SELECT id FROM accounts WHERE phone=?", (phone,))
        r = await row.fetchone()
        return r[0] if r else None


async def get_account(account_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_account_by_phone(phone: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM accounts WHERE phone=?", (phone,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_accounts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM accounts ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_trusted_accounts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM accounts WHERE is_trusted=1 AND has_session=1 ORDER BY score DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_account(account_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [account_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE accounts SET {sets} WHERE id=?", vals)
        await db.commit()


async def add_score(account_id: int, delta: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE accounts SET score = MIN(100, MAX(0, score + ?)) WHERE id=?",
            (delta, account_id)
        )
        await db.commit()


async def log_action(account_id: int, action: str, detail: str = "", score_delta: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO warming_log (account_id, action, detail, score_delta) VALUES (?,?,?,?)",
            (account_id, action, detail, score_delta)
        )
        if score_delta != 0:
            await db.execute(
                "UPDATE accounts SET score = MIN(100, MAX(0, score + ?)), last_active=datetime('now') WHERE id=?",
                (score_delta, account_id)
            )
        await db.commit()


async def get_logs(account_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM warming_log WHERE account_id=? ORDER BY ts DESC LIMIT ?",
            (account_id, limit)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_account(account_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM warming_log WHERE account_id=?", (account_id,))
        await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await db.commit()


async def add_group(chat_id: int, title: str, created_by: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO group_chats (chat_id, title, created_by) VALUES (?,?,?)",
            (chat_id, title, created_by)
        )
        await db.commit()
        return cur.lastrowid


async def add_group_member(group_id: int, account_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, account_id) VALUES (?,?)",
            (group_id, account_id)
        )
        await db.commit()


async def get_all_groups() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM group_chats ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


def get_status_emoji(account: dict) -> str:
    from config import EMOJI, STATUS_THRESHOLDS
    if not account.get("has_session"):
        return EMOJI["black"]
    if account.get("is_trusted") and account.get("score", 0) >= STATUS_THRESHOLDS["green"]:
        return EMOJI["purple"]
    score = account.get("score", 0)
    day = account.get("day", 0)
    if day == 0:
        return EMOJI["white"]
    if score >= STATUS_THRESHOLDS["green"]:
        return EMOJI["green"]
    if score >= STATUS_THRESHOLDS["yellow"]:
        return EMOJI["yellow"]
    return EMOJI["red"]
