"""
exwarmer — web dashboard backend (FastAPI).

Shares the same progrever.db and warming logic as the Telegram bot.
Run with:  uvicorn web.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
import secrets
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
from config import (
    WEB_PASSWORD, SESSIONS_DIR, API_ID, API_HASH,
    STATUS_THRESHOLDS, DEFAULT_HOLD_HOURS, DEFAULT_WARMUP_DAYS,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("exwarmer.web")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="exwarmer")

_tokens: set[str] = set()
_login_sessions: dict[str, dict] = {}   # login_id -> {client, phone, phone_code_hash}


# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────

def check_auth(authorization: str | None = Header(default=None)):
    if not WEB_PASSWORD:
        return True
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Не авторизован")
    token = authorization.split(" ", 1)[1]
    if token not in _tokens:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return True


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody):
    if WEB_PASSWORD and body.password != WEB_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    token = secrets.token_urlsafe(32)
    _tokens.add(token)
    return {"token": token}


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def status_key(acc: dict) -> str:
    if not acc.get("has_session"):
        return "black"
    if acc.get("is_trusted"):
        return "purple"
    score = acc.get("score", 0)
    day   = acc.get("day", 0)
    if day == 0:
        return "white"
    if score >= STATUS_THRESHOLDS["green"]:
        return "green"
    if score >= STATUS_THRESHOLDS["yellow"]:
        return "yellow"
    return "red"


STATUS_LABELS = {
    "green":  "Идеально прогрет",
    "yellow": "Хорошо прогрет",
    "red":    "Плохо прогрет",
    "black":  "Нет сессии",
    "white":  "Новый аккаунт",
    "purple": "Трастовый донор",
}


def mask_phone(phone: str) -> str:
    if not phone or len(phone) <= 7:
        return phone or ""
    return phone[:4] + "***" + phone[-3:]


def next_action_info(acc: dict) -> dict:
    from strategies.warmer import ACTION_LABELS
    action = acc.get("next_action") or "idle"
    label  = ACTION_LABELS.get(action, action)
    at     = acc.get("next_action_at")
    eta_seconds = None
    clock = None
    if at:
        try:
            dt  = datetime.fromisoformat(at)
            clock = dt.strftime("%d.%m %H:%M")
            eta_seconds = max(0, int((dt - datetime.now()).total_seconds()))
        except Exception:
            pass
    return {
        "action": action,
        "label": label,
        "at": at,
        "clock": clock,
        "eta_seconds": eta_seconds,
    }


def serialize_account(acc: dict, full: bool = False) -> dict:
    key = status_key(acc)
    wd  = acc.get("warmup_days") or DEFAULT_WARMUP_DAYS
    day = acc.get("day", 0)
    progress = min(100, round(day / wd * 100)) if wd else acc.get("score", 0)

    hold_hours = acc.get("hold_hours")
    if hold_hours is None:
        hold_hours = DEFAULT_HOLD_HOURS

    hold_remaining = None
    if hold_hours > 0 and acc.get("hold_until"):
        try:
            rem = (datetime.fromisoformat(acc["hold_until"]) - datetime.now()).total_seconds()
            if rem > 0:
                hold_remaining = int(rem)
        except Exception:
            pass

    data = {
        "id": acc["id"],
        "phone": mask_phone(acc["phone"]),
        "phone_raw": acc["phone"],
        "username": acc.get("username"),
        "status": key,
        "status_label": STATUS_LABELS.get(key, ""),
        "score": acc.get("score", 0),
        "day": day,
        "warmup_days": wd,
        "progress": progress,
        "auto_warming": bool(acc.get("auto_warming", 1)),
        "is_trusted": bool(acc.get("is_trusted")),
        "has_session": bool(acc.get("has_session")),
        "hold_hours": hold_hours,
        "hold_enabled": hold_hours > 0,
        "hold_remaining": hold_remaining,
        "warmup_complete": bool(acc.get("warmup_complete")),
        "groups_count": acc.get("groups_count", 0),
        "channels_joined": acc.get("channels_joined", 0),
        "next": next_action_info(acc),
    }
    return data


# ─────────────────────────────────────────────────────────
# Summary & accounts
# ─────────────────────────────────────────────────────────

@app.get("/api/summary")
async def summary(_: bool = Depends(check_auth)):
    accounts = await db.get_all_accounts()
    total    = len(accounts)
    counts: dict[str, int] = {k: 0 for k in STATUS_LABELS}
    for a in accounts:
        counts[status_key(a)] += 1

    with_session = sum(1 for a in accounts if a.get("has_session"))
    auto_active  = sum(1 for a in accounts if a.get("has_session") and a.get("auto_warming", 1))
    complete     = sum(1 for a in accounts if a.get("warmup_complete"))
    avg_score    = round(sum(a.get("score", 0) for a in accounts) / max(total, 1), 1)
    trusted      = sum(1 for a in accounts if a.get("is_trusted"))

    groups = await db.get_all_groups()

    return {
        "total": total,
        "with_session": with_session,
        "auto_active": auto_active,
        "complete": complete,
        "avg_score": avg_score,
        "trusted": trusted,
        "groups": len(groups),
        "status_counts": counts,
    }


@app.get("/api/accounts")
async def accounts(_: bool = Depends(check_auth)):
    rows = await db.get_all_accounts()
    return [serialize_account(a) for a in rows]


@app.get("/api/accounts/{acc_id}")
async def account_detail(acc_id: int, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    logs = await db.get_logs(acc_id, 15)
    data = serialize_account(acc, full=True)
    data["logs"] = [
        {
            "action": lg["action"],
            "detail": lg.get("detail") or lg["action"],
            "score_delta": lg.get("score_delta", 0),
            "ts": (lg.get("ts") or "")[:16],
        }
        for lg in logs
    ]
    return data


# ─────────────────────────────────────────────────────────
# Toggles & settings
# ─────────────────────────────────────────────────────────

@app.post("/api/accounts/{acc_id}/toggle_auto")
async def toggle_auto(acc_id: int, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    new_val = 0 if acc.get("auto_warming", 1) else 1
    await db.update_account(acc_id, auto_warming=new_val)
    from warming_engine import replan_account
    if new_val:
        await replan_account(acc_id)
    else:
        await db.update_account(acc_id, next_action="paused", next_action_at=None)
    return {"auto_warming": bool(new_val)}


@app.post("/api/accounts/{acc_id}/toggle_trust")
async def toggle_trust(acc_id: int, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    new_val = 0 if acc.get("is_trusted") else 1
    await db.update_account(acc_id, is_trusted=new_val)
    from warming_engine import replan_account
    await replan_account(acc_id)
    return {"is_trusted": bool(new_val)}


@app.post("/api/accounts/{acc_id}/toggle_hold")
async def toggle_hold(acc_id: int, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    hold_hours = acc.get("hold_hours")
    if hold_hours is None:
        hold_hours = DEFAULT_HOLD_HOURS
    from strategies.warmer import do_hold
    from warming_engine import replan_account
    if hold_hours > 0:
        await db.update_account(acc_id, hold_hours=0, hold_until=None)
        enabled = False
    else:
        await db.update_account(acc_id, hold_hours=DEFAULT_HOLD_HOURS)
        acc = await db.get_account(acc_id)
        await do_hold(acc)
        enabled = True
    await replan_account(acc_id)
    return {"hold_enabled": enabled}


@app.post("/api/accounts/{acc_id}/hold_restart")
async def hold_restart(acc_id: int, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    from strategies.warmer import do_hold
    from warming_engine import replan_account
    await do_hold(acc)
    await replan_account(acc_id)
    return {"ok": True}


class SettingsBody(BaseModel):
    hold_hours: int | None = None
    warmup_days: int | None = None


@app.post("/api/accounts/{acc_id}/settings")
async def update_settings(acc_id: int, body: SettingsBody, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    updates = {}
    if body.hold_hours is not None and body.hold_hours >= 0:
        updates["hold_hours"] = body.hold_hours
        if body.hold_hours == 0:
            updates["hold_until"] = None
    if body.warmup_days is not None and body.warmup_days >= 1:
        updates["warmup_days"] = body.warmup_days
    if updates:
        await db.update_account(acc_id, **updates)
        from warming_engine import replan_account
        await replan_account(acc_id)
    return {"ok": True}


@app.delete("/api/accounts/{acc_id}")
async def delete_account(acc_id: int, _: bool = Depends(check_auth)):
    await db.delete_account(acc_id)
    return {"ok": True}


# ─────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────

@app.post("/api/accounts/{acc_id}/run_now")
async def run_now(acc_id: int, _: bool = Depends(check_auth)):
    from warming_engine import execute_account_now
    result = await execute_account_now(acc_id)
    return {"result": result}


@app.post("/api/accounts/{acc_id}/action/{name}")
async def run_action(acc_id: int, name: str, _: bool = Depends(check_auth)):
    acc = await db.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Аккаунт не найден")
    import random
    import strategies.warmer as w

    try:
        if name == "spambot":
            ok = await w.do_spambot(acc)
            return {"ok": ok, "msg": "SpamBot выполнено" if ok else "Ошибка SpamBot"}

        if name == "join":
            n = await w.do_join_channel(acc, count=1, with_delay=False, max_flood_wait=25)
            return {"ok": n > 0, "msg": f"Вступил в {n} канал(ов)"}

        if name == "profile":
            ok = await w.do_update_profile(acc)
            return {"ok": ok, "msg": "Профиль обновлён" if ok else "Ошибка"}

        if name == "channel":
            ch = await w.do_create_own_channel(acc)
            return {"ok": ch is not None, "msg": "Канал создан" if ch else "Не удалось"}

        if name == "chpost":
            ok = await w.do_post_to_own_channel(acc)
            return {"ok": ok, "msg": "Пост опубликован" if ok else "Нет канала"}

        if name == "grpmsg":
            groups = await db.get_all_groups()
            if not groups:
                return {"ok": False, "msg": "Нет групп"}
            ok = await w.do_group_message(acc, random.choice(groups)["chat_id"])
            return {"ok": ok, "msg": "Сообщение в группу" if ok else "Ошибка"}

        if name == "create_group":
            all_acc = await db.get_all_accounts()
            peers = [a for a in all_acc if a["id"] != acc_id and a.get("has_session")]
            if not peers:
                return {"ok": False, "msg": "Нет других аккаунтов"}
            members = random.sample(peers, min(random.randint(2, 5), len(peers)))
            ch = await w.do_create_group(acc, members)
            return {"ok": ch is not None, "msg": "Группа создана" if ch else "Не удалось"}

        if name == "dm":
            all_acc = await db.get_all_accounts()
            peers = [a for a in all_acc if a["id"] != acc_id and a.get("has_session")]
            if not peers:
                return {"ok": False, "msg": "Нет других аккаунтов"}
            ok = await w.do_dm_to_peer(acc, random.choice(peers))
            return {"ok": ok, "msg": "Сообщение отправлено" if ok else "Ошибка"}

    except Exception as ex:
        logger.error(f"action {name} for {acc_id}: {ex}")
        return {"ok": False, "msg": f"Ошибка: {ex}"}

    raise HTTPException(400, "Неизвестное действие")


@app.post("/api/run_cycle")
async def run_cycle(_: bool = Depends(check_auth)):
    from warming_engine import run_manual_cycle
    await run_manual_cycle()
    return {"ok": True}


# ─────────────────────────────────────────────────────────
# Add account (interactive login flow)
# ─────────────────────────────────────────────────────────

class AddStartBody(BaseModel):
    phone: str


class AddCodeBody(BaseModel):
    login_id: str
    code: str


class AddPasswordBody(BaseModel):
    login_id: str
    password: str


class AddFinalizeBody(BaseModel):
    login_id: str | None = None
    phone: str | None = None
    is_trusted: bool = False
    hold_hours: int = DEFAULT_HOLD_HOURS
    warmup_days: int = DEFAULT_WARMUP_DAYS


@app.post("/api/add/start")
async def add_start(body: AddStartBody, _: bool = Depends(check_auth)):
    from pyrogram import Client as PyroClient
    from pyrogram.errors import (
        FloodWait, PhoneNumberInvalid, PhoneNumberBanned,
    )

    phone = body.phone.strip()
    if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
        raise HTTPException(400, "Неверный формат номера. Пример: +79001234567")

    session_file = phone.replace("+", "")
    session_path = os.path.join(SESSIONS_DIR, session_file + ".session")

    if os.path.exists(session_path):
        return {"stage": "exists", "phone": phone}

    os.makedirs(SESSIONS_DIR, exist_ok=True)
    client = PyroClient(
        name=os.path.join(SESSIONS_DIR, session_file),
        api_id=API_ID,
        api_hash=API_HASH,
    )
    try:
        await client.connect()
        sent = await client.send_code(phone)
    except FloodWait as e:
        raise HTTPException(429, f"Флуд-лимит, подождите {e.value} сек")
    except PhoneNumberBanned:
        raise HTTPException(400, "Номер заблокирован в Telegram")
    except PhoneNumberInvalid:
        raise HTTPException(400, "Номер не распознан Telegram")
    except Exception as ex:
        raise HTTPException(400, f"Ошибка: {ex}")

    login_id = secrets.token_urlsafe(16)
    _login_sessions[login_id] = {
        "client": client,
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
    }
    return {"stage": "code", "login_id": login_id, "phone": phone}


@app.post("/api/add/code")
async def add_code(body: AddCodeBody, _: bool = Depends(check_auth)):
    import re
    from pyrogram.errors import (
        SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    )

    sess = _login_sessions.get(body.login_id)
    if not sess:
        raise HTTPException(400, "Сессия входа истекла, начните заново")

    code = re.sub(r"\D", "", body.code)
    if not code:
        raise HTTPException(400, "Введите цифры кода")

    client = sess["client"]
    try:
        await client.sign_in(sess["phone"], sess["phone_code_hash"], code)
        await client.disconnect()
        phone = sess["phone"]
        _login_sessions.pop(body.login_id, None)
        return {"stage": "done", "phone": phone}
    except SessionPasswordNeeded:
        return {"stage": "2fa", "login_id": body.login_id}
    except PhoneCodeInvalid:
        raise HTTPException(400, "Неверный код")
    except PhoneCodeExpired:
        try:
            await client.disconnect()
        except Exception:
            pass
        _login_sessions.pop(body.login_id, None)
        raise HTTPException(400, "Код устарел, начните заново")
    except Exception as ex:
        raise HTTPException(400, f"Ошибка: {ex}")


@app.post("/api/add/password")
async def add_password(body: AddPasswordBody, _: bool = Depends(check_auth)):
    sess = _login_sessions.get(body.login_id)
    if not sess:
        raise HTTPException(400, "Сессия входа истекла, начните заново")
    client = sess["client"]
    try:
        await client.check_password(body.password)
        await client.disconnect()
        phone = sess["phone"]
        _login_sessions.pop(body.login_id, None)
        return {"stage": "done", "phone": phone}
    except Exception as ex:
        raise HTTPException(400, f"Неверный пароль: {ex}")


@app.post("/api/add/finalize")
async def add_finalize(body: AddFinalizeBody, _: bool = Depends(check_auth)):
    phone = body.phone
    if not phone and body.login_id:
        sess = _login_sessions.get(body.login_id)
        if sess:
            phone = sess["phone"]
    if not phone:
        raise HTTPException(400, "Телефон не указан")

    session_file = phone.replace("+", "")
    session_path = os.path.join(SESSIONS_DIR, session_file + ".session")
    has_session  = os.path.exists(session_path)

    acc_id = await db.add_account(
        phone=phone,
        session_file=session_file,
        strategy=1,
        is_trusted=body.is_trusted,
        hold_hours=max(0, body.hold_hours),
        warmup_days=max(1, body.warmup_days),
    )
    updates = {
        "has_session": 1 if has_session else 0,
        "hold_hours": max(0, body.hold_hours),
        "warmup_days": max(1, body.warmup_days),
    }
    if body.hold_hours <= 0:
        updates["hold_until"] = None
    await db.update_account(acc_id, **updates)

    if has_session:
        from warming_engine import init_account_warming
        await init_account_warming(acc_id)

    return {"ok": True, "id": acc_id, "has_session": has_session}


# ─────────────────────────────────────────────────────────
# Static frontend
# ─────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")


async def _startup():
    await db.init_db()


@app.on_event("startup")
async def on_startup():
    await _startup()
