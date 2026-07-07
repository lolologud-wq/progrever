"""
Warming Engine — schedules and runs warming cycles for all accounts.
Uses APScheduler for periodic jobs.
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import database as db
from strategies.manual1 import step_account_manual1, run_hold_phase
from strategies.manual2 import step_account_manual2
from config import STATUS_THRESHOLDS

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# Callback to notify the management bot (set from main.py)
notify_callback = None


def set_notify_callback(fn):
    global notify_callback
    notify_callback = fn


async def _notify(text: str):
    if notify_callback:
        try:
            await notify_callback(text)
        except Exception as e:
            logger.warning(f"Notify error: {e}")


async def run_warming_cycle():
    """Main daily warming cycle — runs all active accounts."""
    logger.info("Warming cycle started")
    accounts = await db.get_all_accounts()
    trusted = await db.get_trusted_accounts()

    results = []
    for acc in accounts:
        if not acc.get("has_session"):
            continue
        if acc.get("is_trusted") and acc.get("score", 0) >= STATUS_THRESHOLDS["green"]:
            continue  # trusted accounts are donors, skip their own warming

        strategy = acc.get("strategy", 1)
        try:
            if strategy == 1:
                ok = await step_account_manual1(acc, trusted)
            else:
                peers = [a for a in accounts if a["id"] != acc["id"]]
                ok = await step_account_manual2(acc, peers)

            results.append((acc["phone"], ok))
            logger.info(f"Account {acc['phone']}: {'OK' if ok else 'skipped'}")
        except Exception as e:
            logger.error(f"Error warming {acc['phone']}: {e}")
            await db.log_action(acc["id"], "engine_error", str(e))

    done = sum(1 for _, ok in results if ok)
    await _notify(
        f"🔥 Цикл прогрева завершён\n"
        f"Аккаунтов обработано: {done}/{len(results)}"
    )


async def init_account_warming(account_id: int):
    """Start warming for a newly added account."""
    acc = await db.get_account(account_id)
    if not acc:
        return
    strategy = acc.get("strategy", 1)
    if strategy == 1:
        await run_hold_phase(acc)
    else:
        await db.update_account(account_id, day=1)
        await db.log_action(account_id, "warming_start", "Manual 2 warming started", 2)


def start_scheduler():
    """Start the APScheduler background scheduler."""
    # Run warming cycle every day at 10:00 and 18:00
    scheduler.add_job(
        run_warming_cycle,
        CronTrigger(hour="10,18", minute=0),
        id="warming_cycle",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info("Scheduler started (warming at 10:00 and 18:00)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


async def run_manual_cycle():
    """Trigger warming cycle manually (from bot command)."""
    await run_warming_cycle()
