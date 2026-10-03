import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.mongodb import get_collection
from app.websocket.manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()
stations = get_collection("charging_stations")

OFFLINE_TIMEOUT_SECONDS = 45
CHECK_INTERVAL_SECONDS = 15


async def run_station_watchdog() -> None:
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(seconds=OFFLINE_TIMEOUT_SECONDS)
            stale = {
                "is_online": True,
                "last_heartbeat_at": {"$lt": cutoff},
                # Mock stations on the map have no hardware sending heartbeats.
                "is_simulated": {"$ne": True},
            }
            gone = [s["station_id"] async for s in stations.find(stale, {"_id": 0, "station_id": 1})]
            if gone:
                await stations.update_many(
                    {**stale, "station_id": {"$in": gone}},
                    {"$set": {"is_online": False, "updated_at": datetime.now(UTC)}},
                )
                logger.warning("Marked %s station(s) offline: %s", len(gone), ", ".join(gone))
                # Apps and the web admin refresh the station without polling.
                for station_id in gone:
                    await manager.broadcast(
                        {"type": "station_updated", "station_id": station_id, "is_online": False, "availability": "OFFLINE"}
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Station watchdog failed")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
