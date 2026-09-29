import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.mongodb import get_collection

logger = logging.getLogger(__name__)
settings = get_settings()
stations = get_collection("charging_stations")

OFFLINE_TIMEOUT_SECONDS = 45
CHECK_INTERVAL_SECONDS = 15


async def run_station_watchdog() -> None:
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(seconds=OFFLINE_TIMEOUT_SECONDS)
            result = await stations.update_many(
                {
                    "is_online": True,
                    "last_heartbeat_at": {"$lt": cutoff},
                },
                {
                    "$set": {
                        "is_online": False,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
            if result.modified_count:
                logger.warning("Marked %s station(s) offline", result.modified_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Station watchdog failed")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
