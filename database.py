from __future__ import annotations

import json
import aiosqlite
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
    strategy INTEGER DEFAULT 1,
    is_trusted INTEGER DEFAULT 0,
    has_session INTEGER DEFAULT 0,
    auto_warming INTEGER DEFAULT 1,
    score INTEGER DEFAULT 0,
    day INTEGER DEFAULT 0,
    hold_hours INTEGER DEFAULT 24,
    warmup_days INTEGER DEFAULT 10,
    hold_until TEXT,
    profile_changed INTEGER DEFAULT 0,
    channels_joined INTEGER DEFAULT 0,
    groups_count INTEGER DEFAULT 0,
    own_channels TEXT DEFAULT '[]',
    warmup_complete INTEGER DEFAULT 0,
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
    created_by INTEGER,
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

CREATE TABLE IF NOT EXISTS pending_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    to_user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    respond_after TEXT NOT NULL,
    done INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_MIGRATIONS = [
    ("accounts", "auto_warming",       "INTEGER DEFAULT 1"),
    ("accounts", "channels_joined",    "INTEGER DEFAULT 0"),
    ("accounts", "groups_count",       "INTEGER DEFAULT 0"),
    ("accounts", "hold_hours",         "INTEGER DEFAULT 24"),
    ("accounts", "warmup_days",        "INTEGER DEFAULT 10"),
    ("accounts", "own_channels",       "TEXT DEFAULT '[]'"),
    ("accounts", "warmup_complete",    "INTEGER DEFAULT 0"),
]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(CREATE_TABLES)
        for table, column, definition in _MIGRATIONS:
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception:
                pass
        await conn.commit()


async def add_account(
    phone: str,
    session_file: str = None,
    strategy: int = 1,
    is_trusted: bool = False,
    hold_hours: int = 24,
    warmup_days: int = 10,
) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO accounts
               (phone, session_file, strategy, is_trusted, hold_hours, warmup_days)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET
                 session_file=COALESCE(excluded.session_file, accounts.session_file),
                 strategy=excluded.strategy,
                 is_trusted=excluded.is_trusted,
                 hold_hours=excluded.hold_hours,
                 warmup_days=excluded.warmup_days""",
            (phone, session_file, strategy, 1 if is_trusted else 0, hold_hours, warmup_days),
        )
        await conn.commit()
        row = await (await conn.execute(
            "SELECT id FROM accounts WHERE phone=?", (phone,)
        )).fetchone()
        return row[0] if row else None


async def get_account(account_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_account_by_phone(phone: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_accounts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM accounts ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_trusted_accounts() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM accounts WHERE is_trusted=1 AND has_session=1 ORDER BY score DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_account(account_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [account_id]
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(f"UPDATE accounts SET {sets} WHERE id=?", vals)
        await conn.commit()


async def add_score(account_id: int, delta: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE accounts SET score = MIN(100, MAX(0, score + ?)) WHERE id=?",
            (delta, account_id),
        )
        await conn.commit()


async def log_action(
    account_id: int,
    action: str,
    detail: str = "",
    score_delta: int = 0,
):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO warming_log (account_id, action, detail, score_delta) VALUES (?,?,?,?)",
            (account_id, action, detail, score_delta),
        )
        if score_delta != 0:
            await conn.execute(
                "UPDATE accounts SET score = MIN(100, MAX(0, score + ?)), "
                "last_active=datetime('now') WHERE id=?",
                (score_delta, account_id),
            )
        await conn.commit()


async def get_logs(account_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM warming_log WHERE account_id=? ORDER BY ts DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_account(account_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM warming_log WHERE account_id=?", (account_id,))
        await conn.execute("DELETE FROM group_members WHERE account_id=?", (account_id,))
        await conn.execute("DELETE FROM pending_responses WHERE account_id=?", (account_id,))
        await conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await conn.commit()


async def add_group(chat_id: int, title: str, created_by: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO group_chats (chat_id, title, created_by) VALUES (?,?,?)",
            (chat_id, title, created_by),
        )
        await conn.commit()
        return cur.lastrowid


async def add_group_member(group_id: int, account_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, account_id) VALUES (?,?)",
            (group_id, account_id),
        )
        await conn.commit()


async def get_all_groups() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM group_chats ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_pending_response(
    account_id: int,
    to_user_id: int,
    message: str,
    delay_seconds: int,
):
    from datetime import timedelta
    respond_after = (
        datetime.now() + timedelta(seconds=delay_seconds)
    ).isoformat()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO pending_responses (account_id, to_user_id, message, respond_after) "
            "VALUES (?,?,?,?)",
            (account_id, to_user_id, message, respond_after),
        )
        await conn.commit()


async def get_due_responses(account_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM pending_responses "
            "WHERE account_id=? AND done=0 AND respond_after <= datetime('now') "
            "ORDER BY respond_after LIMIT 3",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def mark_response_done(response_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE pending_responses SET done=1 WHERE id=?", (response_id,)
        )
        await conn.commit()


def get_own_channels(account: dict) -> list[int]:
    try:
        return json.loads(account.get("own_channels") or "[]")
    except Exception:
        return []


async def save_own_channels(account_id: int, channel_ids: list[int]):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE accounts SET own_channels=? WHERE id=?",
            (json.dumps(channel_ids), account_id),
        )
        await conn.commit()


def get_status_emoji(account: dict) -> str:
    from config import EMOJI, STATUS_THRESHOLDS
    if not account.get("has_session"):
        return EMOJI["black"]
    if account.get("is_trusted"):
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


def get_status_label(account: dict) -> str:
    from config import EMOJI
    emoji = get_status_emoji(account)
    labels = {
        EMOJI["green"]:  "Идеально прогрет",
        EMOJI["yellow"]: "Хорошо прогрет",
        EMOJI["red"]:    "Плохо прогрет",
        EMOJI["black"]:  "Нет сессии",
        EMOJI["white"]:  "Новый аккаунт",
        EMOJI["purple"]: "Трастовый донор",
    }
    return labels.get(emoji, "Неизвестно")
