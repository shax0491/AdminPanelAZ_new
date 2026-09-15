"""Periodic auto-switch for dnat_front failover pools.

Without this, ``mode=auto`` on a pool was purely cosmetic: ``evaluate_and_switch``
(app/services/failover_front.py) only ever ran when an admin clicked "Проверить
и переключить" in the UI. A real server outage between clicks would sit there
un-failed-over indefinitely — the exact scenario the feature exists for.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.database import SessionLocal
from app.models import FailoverPool, FailoverPoolMode, FailoverPoolStrategy
from app.services.failover_front import evaluate_and_switch

logger = logging.getLogger(__name__)


def run_failover_scheduler_tick(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        pools = (
            db.query(FailoverPool)
            .filter(
                FailoverPool.enabled.is_(True),
                FailoverPool.mode == FailoverPoolMode.auto,
                FailoverPool.strategy == FailoverPoolStrategy.dnat_front,
            )
            .all()
        )
        results: list[dict] = []
        for pool in pools:
            try:
                result = evaluate_and_switch(db, pool)
                if result.get("switched"):
                    logger.warning(
                        "failover_scheduler: pool %s auto-switched to member %s",
                        pool.id,
                        result.get("active_member_id"),
                    )
                results.append({"pool_id": pool.id, **result})
            except Exception as exc:  # noqa: BLE001 — one pool's failure must not skip the rest
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.exception("failover_scheduler: pool %s tick failed: %s", pool.id, exc)
                results.append({"pool_id": pool.id, "status": "error", "error": str(exc)})
        return results
    except Exception as exc:
        logger.exception("failover_scheduler: tick failed: %s", exc)
        return [{"status": "error", "error": str(exc)}]
    finally:
        db.close()


async def run_failover_scheduler_loop() -> None:
    while True:
        try:
            settings = get_settings()
            interval = max(15, int(getattr(settings, "failover_scheduler_interval_seconds", 20) or 20))
            await asyncio.sleep(interval)
            if not getattr(settings, "failover_scheduler_enabled", True):
                logger.debug("failover_scheduler skipped — FAILOVER_SCHEDULER_ENABLED disabled")
                continue
            results = await asyncio.to_thread(run_failover_scheduler_tick)
            for result in results:
                if result.get("switched"):
                    logger.info("failover_scheduler tick: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("failover_scheduler loop error: %s", exc)
