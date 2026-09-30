import asyncio
from datetime import datetime, timezone

from app.db.mongodb import close_database, get_collection


async def main() -> None:
    stations = get_collection("charging_stations")
    now = datetime.now(timezone.utc)
    await stations.update_one(
        {"station_id": "EVCS-01"},
        {
            "$set": {
                "name": "SECO Station 01",
                "status": "READY",
                "is_online": False,
                "connector_lock": "UNLOCKED",
                "current_session_id": None,
                "last_heartbeat_at": None,
                "latitude": 16.0544,
                "longitude": 108.2022,
                "updated_at": now,
            },
            "$setOnInsert": {
                "station_id": "EVCS-01",
                "created_at": now,
            },
        },
        upsert=True,
    )
    print("Seeded EVCS-01")
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
