"""
warming_engine.py — APScheduler-based engine with human-like random schedule.

Key design:
  - Job runs every 20–40 minutes (randomised jitter via IntervalTrigger).
  - Each account independently decides whether to act based on hour-of-day
    probability (HOUR_ACTIVITY) + a per-account offset so they don't all
    fire at the same second.
  - Trusted accounts are NEVER warmed — they only write to warming accounts
    (handled via do_all_trusted_writes).
  - Completion notification is sent to admin when day >= warmup_days.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from config import HOUR_ACTIVITY, ADMIN_IDS

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot_app = None           # set in main.py after app is built
_cycle_lock = asyncio.Lock()


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _should_act(account_id: int, hour: int) -> bool:
    """Return True if this account should be active in the current hour."""
    base_prob = HOUR_ACTIVITY.get(hour, 0.3)
    # Each account gets a small random personal modifier (-0.15 … +0.15)
    rng   = random.Random(account_id)
    mod   = rng.uniform(-0.15, 0.15)
    prob  = max(0.0, min(1.0, base_prob + mod))
    return random.random() < prob


async def _notify_admins(text: str):
    if _bot_app is None:
        return
    for admin_id in ADMIN_IDS:
        try:
            await _bot_app.bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as ex:
            logger.warning(f"Admin notify failed for {admin_id}: {ex}")


async def _check_warmup_completion(account: dict):
    if account.get("warmup_complete"):
        return
    wd  = account.get("warmup_days") or 10
    day = account.get("day", 0)
    if day >= wd:
        await db.update_account(account["id"], warmup_complete=1, auto_warming=0)
        phone = account.get("username") or account["phone"]
        await _notify_admins(
            f"✅ <b>Прогрев завершён!</b>\n\n"
            f"Аккаунт: <b>{phone}</b>\n"
            f"Дней прогрева: {day}\n"
            f"Прогресс: {account.get('score', 0)}%\n\n"
            f"Авто-прогрев отключён."
        )
        logger.info(f"[{account['phone']}] warmup complete at day {day}")


# ─────────────────────────────────────────────────────────
# Core cycle
# ─────────────────────────────────────────────────────────

async def _run_cycle_inner():
    now  = datetime.now()
    hour = now.hour

    all_accounts  = await db.get_all_accounts()
    trusted_list  = await db.get_trusted_accounts()
    warming_list  = [
        a for a in all_accounts
        if not a.get("is_trusted")
        and a.get("has_session")
        and a.get("auto_warming", 1)
        and not a.get("warmup_complete")
    ]

    if not warming_list and not trusted_list:
        return

    # ── Trusted accounts write to warming accounts ────────
    if trusted_list and warming_list:
        # At "active" hours, trusted writes at ~30% probability per cycle
        t_prob = HOUR_ACTIVITY.get(hour, 0.3) * 0.4
        if random.random() < t_prob:
            from strategies.warmer import do_all_trusted_writes
            try:
                n = await do_all_trusted_writes(trusted_list, warming_list)
                logger.info(f"Trusted writes: {n}")
            except Exception as ex:
                logger.error(f"trusted writes error: {ex}")

    # ── Warming accounts — each decides independently ──────
    acts = [a for a in warming_list if _should_act(a["id"], hour)]
    if not acts:
        logger.debug(f"Hour {hour}: no accounts decided to act this cycle")
        return

    random.shuffle(acts)  # prevent always same order

    from strategies.warmer import step_account
    for acc in acts:
        try:
            fresh = await db.get_account(acc["id"])
            if not fresh:
                continue
            await _check_warmup_completion(fresh)
            if fresh.get("warmup_complete"):
                continue
            await step_account(fresh, trusted_list, warming_list, hour)
            # Small gap between accounts
            await asyncio.sleep(random.uniform(30, 90))
        except Exception as ex:
            logger.error(f"[{acc['phone']}] cycle error: {ex}")


async def run_warming_cycle():
    """Called by scheduler — acquires lock so cycles don't stack."""
    if _cycle_lock.locked():
        logger.info("Cycle already running, skipping this tick")
        return
    async with _cycle_lock:
        try:
            await _run_cycle_inner()
        except Exception as ex:
            logger.error(f"Warming cycle error: {ex}")


async def run_manual_cycle():
    """Called from bot 'Прогреть сейчас' button — no scheduling lock."""
    await _run_cycle_inner()


# ─────────────────────────────────────────────────────────
# Initialise new account
# ─────────────────────────────────────────────────────────

async def init_account_warming(account_id: int):
    """Called after a new account is added — starts the hold phase."""
    acc = await db.get_account(account_id)
    if not acc:
        return
    if acc.get("is_trusted"):
        logger.info(f"Skip warming init for trusted account {acc['phone']}")
        return
    from strategies.warmer import do_hold
    await do_hold(acc)


# ─────────────────────────────────────────────────────────
# Scheduler lifecycle
# ─────────────────────────────────────────────────────────

def init_scheduler(bot_app) -> AsyncIOScheduler:
    global _scheduler, _bot_app
    _bot_app   = bot_app
    _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Random interval: base 25 min, jitter ±10 min = 15–35 min between runs
    _scheduler.add_job(
        run_warming_cycle,
        IntervalTrigger(minutes=25, jitter=600),
        id="warming_cycle",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("Scheduler started (interval ~25min ±10min)")
    return _scheduler


def stop_scheduler():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
