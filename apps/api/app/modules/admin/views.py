"""Shape Mongo documents for the admin API, resolving user and station names."""

from app.modules.admin.common import station_names, user_names
from app.modules.charging.usage import serialize_session, session_live_fields
from app.modules.station.service import station_availability


def card_status(card: dict) -> str:
    if card.get("pending"):
        return "PENDING"
    if not card.get("user_id"):
        return "UNASSIGNED"
    return "ACTIVE" if card.get("is_active") else "INACTIVE"


def session_view(session: dict, users: dict, stations: dict) -> dict:
    return {
        **serialize_session(session),
        **session_live_fields(session),
        "user_name": users.get(session.get("user_id")),
        "station_name": stations.get(session["station_id"]),
    }


async def session_views(items: list[dict]) -> list[dict]:
    users = await user_names(s.get("user_id") for s in items)
    stations = await station_names(s["station_id"] for s in items)
    return [session_view(s, users, stations) for s in items]


async def card_views(items: list[dict]) -> list[dict]:
    users = await user_names(c.get("user_id") for c in items)
    return [
        {**c, "user_name": users.get(c.get("user_id")), "status": card_status(c)}
        for c in items
    ]


async def transaction_views(items: list[dict]) -> list[dict]:
    users = await user_names(t.get("user_id") for t in items)
    return [{**t, "user_name": users.get(t.get("user_id"))} for t in items]


async def payment_views(items: list[dict]) -> list[dict]:
    stations = await station_names(p.get("station_id") for p in items)
    return [{**p, "station_name": stations.get(p.get("station_id"))} for p in items]


async def hold_views(items: list[dict]) -> list[dict]:
    users = await user_names(h.get("user_id") for h in items)
    return [{**h, "user_name": users.get(h.get("user_id"))} for h in items]


async def maintenance_views(items: list[dict]) -> list[dict]:
    users = await user_names(m.get("technician_id") for m in items)
    stations = await station_names(m["station_id"] for m in items)
    return [
        {
            **m,
            "technician_name": users.get(m.get("technician_id")),
            "station_name": stations.get(m["station_id"]),
        }
        for m in items
    ]


async def error_views(items: list[dict]) -> list[dict]:
    users = await user_names(e.get("resolved_by") for e in items)
    stations = await station_names(e["station_id"] for e in items)
    return [
        {
            **e,
            "resolved_by_name": users.get(e.get("resolved_by")),
            "station_name": stations.get(e["station_id"]),
        }
        for e in items
    ]


def station_view(station: dict, session_status: str | None) -> dict:
    return {**station, "availability": station_availability(station, session_status)}
