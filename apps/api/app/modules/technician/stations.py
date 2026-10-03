"""What a technician needs to fix a station: its live state, open errors,
member requests and the maintenance done on it (read only; a technician changes
a station's mode at the station itself, RFID card + STOP held 3 s)."""

from typing import Any

from app.core.exceptions import ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.station.service import get_station, list_stations
from app.modules.support.service import OPEN_STATUSES, ticket_views

error_logs = get_collection("error_logs")
maintenance_records = get_collection("maintenance_records")
tickets = get_collection("support_tickets")
users = get_collection("users")

_ERROR_FIELDS = {
    "_id": 0,
    "error_id": 1,
    "station_id": 1,
    "session_id": 1,
    "error_type": 1,
    "severity": 1,
    "message": 1,
    "details": 1,
    "occurred_at": 1,
}


async def _count_by_station(collection, query: dict) -> dict[str, int]:
    pipeline = [{"$match": query}, {"$group": {"_id": "$station_id", "n": {"$sum": 1}}}]
    return {row["_id"]: row["n"] async for row in await collection.aggregate(pipeline)}


async def list_stations_for_technician() -> list[dict[str, Any]]:
    """Every station with how much work is waiting there; stations needing a visit first."""
    open_errors = await _count_by_station(error_logs, {"resolved_at": None})
    open_tickets = await _count_by_station(tickets, {"status": {"$in": list(OPEN_STATUSES)}})
    items = [
        {
            **station,
            "open_error_count": open_errors.get(station["station_id"], 0),
            "open_ticket_count": open_tickets.get(station["station_id"], 0),
            "temperature_c": (station.get("latest_telemetry") or {}).get("temperature_c"),
        }
        for station in await list_stations()
    ]
    urgency = {"ERROR": 0, "MAINTENANCE": 1, "OFFLINE": 2}
    items.sort(
        key=lambda s: (
            urgency.get(s["availability"], 3),
            -(s["open_error_count"] + s["open_ticket_count"]),
            s["station_id"],
        )
    )
    return items


async def _maintenance_views(items: list[dict]) -> list[dict]:
    ids = list({m["technician_id"] for m in items})
    names = {
        u["user_id"]: u["name"]
        async for u in users.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "name": 1})
    }
    return [{**m, "technician_name": names.get(m["technician_id"])} for m in items]


async def station_detail_for_technician(station_id: str) -> dict[str, Any]:
    station = await get_station(station_id)
    if not station:
        raise ResourceNotFoundError("Station not found")

    errors = await error_logs.find(
        {"station_id": station_id, "resolved_at": None}, _ERROR_FIELDS
    ).sort("occurred_at", -1).to_list(length=20)
    open_tickets = await tickets.find(
        {"station_id": station_id, "status": {"$in": list(OPEN_STATUSES)}}, {"_id": 0}
    ).sort("created_at", -1).to_list(length=20)
    maintenance = await maintenance_records.find(
        {"station_id": station_id}, {"_id": 0}
    ).sort("started_at", -1).to_list(length=10)

    return {
        "station": station,
        "latest_telemetry": station.get("latest_telemetry"),
        "open_errors": errors,
        "open_tickets": await ticket_views(open_tickets),
        "maintenance": await _maintenance_views(maintenance),
    }


async def list_my_maintenance(*, technician_id: str, limit: int, offset: int) -> dict[str, Any]:
    """Enter / exit maintenance done by this technician at the stations."""
    query = {"technician_id": technician_id}
    items = await maintenance_records.find(query, {"_id": 0}).sort("started_at", -1).skip(offset).limit(limit).to_list(
        length=limit
    )
    return {
        "items": await _maintenance_views(items),
        "total": await maintenance_records.count_documents(query),
        "limit": limit,
        "offset": offset,
    }
