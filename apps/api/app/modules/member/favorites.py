"""Stations a member pinned for quick access in the app."""

from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.station.service import list_stations_by_ids

favorite_stations = get_collection("favorite_stations")
stations = get_collection("charging_stations")


async def list_favorites(*, user_id: str) -> list[dict]:
    """Newest first, with each station's live availability."""
    favorites = [
        f
        async for f in favorite_stations.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    ]
    if not favorites:
        return []

    by_id = {s["station_id"]: s for s in await list_stations_by_ids([f["station_id"] for f in favorites])}
    # A deleted station simply drops out of the list.
    return [
        {**by_id[f["station_id"]], "favorited_at": f["created_at"]}
        for f in favorites
        if f["station_id"] in by_id
    ]


async def add_favorite(*, user_id: str, station_id: str) -> None:
    """Idempotent: pinning an already pinned station is not an error."""
    if not await stations.find_one({"station_id": station_id}, {"_id": 1}):
        raise ResourceNotFoundError("Station not found")
    try:
        await favorite_stations.insert_one(
            {"user_id": user_id, "station_id": station_id, "created_at": datetime.now(UTC)}
        )
    except DuplicateKeyError:
        pass


async def remove_favorite(*, user_id: str, station_id: str) -> None:
    await favorite_stations.delete_one({"user_id": user_id, "station_id": station_id})
