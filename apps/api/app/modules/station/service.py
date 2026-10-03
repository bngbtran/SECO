from enum import StrEnum

from app.db.mongodb import get_collection
from app.modules.charging.constants import SessionStatus, StationStatus

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")


class StationAvailability(StrEnum):
    """What a driver sees on the map / station screen (UC-U05, UC-G01)."""

    AVAILABLE = "AVAILABLE"
    CHARGING = "CHARGING"
    WAITING_PAYMENT = "WAITING_PAYMENT"
    ERROR = "ERROR"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"


def station_availability(station: dict, session_status: str | None) -> str:
    if not station.get("is_online", False):
        return StationAvailability.OFFLINE.value

    status = station.get("status")
    if status == StationStatus.MAINTENANCE.value:
        return StationAvailability.MAINTENANCE.value
    if status == StationStatus.ERROR.value:
        return StationAvailability.ERROR.value
    if session_status == SessionStatus.WAITING_PAYMENT.value:
        return StationAvailability.WAITING_PAYMENT.value
    if station.get("current_session_id") or status == StationStatus.CHARGING.value:
        return StationAvailability.CHARGING.value
    return StationAvailability.AVAILABLE.value


async def _with_availability(items: list[dict]) -> list[dict]:
    session_ids = [s["current_session_id"] for s in items if s.get("current_session_id")]
    statuses = {
        session["session_id"]: session["status"]
        async for session in sessions.find(
            {"session_id": {"$in": session_ids}},
            {"_id": 0, "session_id": 1, "status": 1},
        )
    } if session_ids else {}

    return [
        {
            **station,
            "availability": station_availability(
                station,
                statuses.get(station.get("current_session_id")),
            ),
        }
        for station in items
    ]


async def list_stations() -> list[dict]:
    cursor = stations.find({}, {"_id": 0}).sort("station_id", 1)
    return await _with_availability(await cursor.to_list(length=500))


async def list_stations_by_ids(station_ids: list[str]) -> list[dict]:
    cursor = stations.find({"station_id": {"$in": station_ids}}, {"_id": 0})
    return await _with_availability(await cursor.to_list(length=len(station_ids)))


async def get_station(station_id: str) -> dict | None:
    station = await stations.find_one({"station_id": station_id}, {"_id": 0})
    if not station:
        return None
    return (await _with_availability([station]))[0]
