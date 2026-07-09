"""
warming_engine.py — per-account auto-warming scheduler.

Each account has a planned next_action + next_action_at.
A job runs every minute and executes due actions automatically.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot_app = None
_action_lock = asyncio.Lock()


async def _notify_admins(text: str):
    if _bot_app is None:
        return
    for admin_id in ADMIN_IDS:
        try:
            await _bot_app.bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as ex:
            logger.warning(f"Admin notify failed for {admin_id}: {ex}")


async def check_warmup_completion(account: dict):
    if account.get("warmup_complete"):
        return
    wd  = account.get("warmup_days") or 10
    day = account.get("day", 0)
    if day >= wd:
        await db.update_account(
            account["id"],
            warmup_complete=1,
            auto_warming=0,
            next_action="idle",
            next_action_at=None,
        )
        phone = account.get("username") or account["phone"]
        await _notify_admins(
            f"✅ <b>Прогрев завершён!</b>\n\n"
            f"Аккаунт: <b>{phone}</b>\n"
            f"Дней прогрева: {day}\n"
            f"Прогресс: {account.get('score', 0)}%\n\n"
            f"Авто-прогрев отключён."
        )
        logger.info(f"[{account['phone']}] warmup complete at day {day}")


async def _process_due_actions():
    from strategies.warmer import plan_next_action, run_account_action

    due = await db.get_accounts_due_for_action()
    for acc in due:
        try:
            fresh = await db.get_account(acc["id"])
            if not fresh or not fresh.get("auto_warming", 1):
                continue

            action = fresh.get("next_action")
            if action == "hold_wait":
                from strategies.warmer import _is_in_hold
                if _is_in_hold(fresh):
                    continue
                await plan_next_action(fresh["id"])
                continue

            msg = await run_account_action(fresh["id"])
            logger.info(f"[{fresh['phone']}] auto-run: {msg}")
            await asyncio.sleep(5)
        except Exception as ex:
            logger.error(f"[{acc.get('phone')}] due action error: {ex}")


async def ensure_schedules():
    from strategies.warmer import plan_next_action

    unscheduled = await db.get_accounts_without_schedule()
    for acc in unscheduled:
        try:
            await plan_next_action(acc["id"])
        except Exception as ex:
            logger.error(f"plan error [{acc.get('phone')}]: {ex}")


async def _ensure_schedules():
    await ensure_schedules()


async def process_scheduler_tick():
    if _action_lock.locked():
        return
    async with _action_lock:
        try:
            await _ensure_schedules()
            await _process_due_actions()
        except Exception as ex:
            logger.error(f"Scheduler tick error: {ex}")


async def run_warming_cycle():
    await process_scheduler_tick()


async def run_manual_cycle():
    """Run all due actions immediately for every auto-warming account."""
    from strategies.warmer import plan_next_action, run_account_action

    accounts = await db.get_all_accounts()
    targets = [
        a for a in accounts
        if a.get("has_session")
        and a.get("auto_warming", 1)
        and not a.get("warmup_complete")
    ]
    for acc in targets:
        try:
            await run_account_action(acc["id"])
            await asyncio.sleep(3)
        except Exception as ex:
            logger.error(f"manual cycle [{acc['phone']}]: {ex}")


async def execute_account_now(account_id: int) -> str:
    from strategies.warmer import run_account_action
    return await run_account_action(account_id)


async def init_account_warming(account_id: int):
    from strategies.warmer import do_hold, plan_next_action

    acc = await db.get_account(account_id)
    if not acc:
        return
    if acc.get("is_trusted"):
        await plan_next_action(account_id)
        return
    await do_hold(acc)
    await plan_next_action(account_id)


async def replan_account(account_id: int):
    from strategies.warmer import plan_next_action
    await plan_next_action(account_id)


def init_scheduler(bot_app) -> AsyncIOScheduler:
    global _scheduler, _bot_app
    _bot_app   = bot_app
    _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    _scheduler.add_job(
        process_scheduler_tick,
        IntervalTrigger(minutes=1),
        id="warming_actions",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("Scheduler started (per-account actions, tick every 1 min)")
    return _scheduler


def stop_scheduler():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
