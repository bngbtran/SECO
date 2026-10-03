"""Member help requests at a station, handled by technicians.

A member files a ticket for a station; the API stores a snapshot of the station
as it was at that moment (status, link, telemetry, open errors, the member's
session there), writes an audit entry and notifies every technician.
OPEN -> IN_PROGRESS (a technician accepts it) -> RESOLVED (with a note);
the member can cancel while it is still OPEN.

The real station (hardware) is put back in service only at the station itself
(technician card + STOP held 3 s: ERROR -> MAINTENANCE -> READY, which also
closes its open errors), so its request cannot be resolved while it is still
in ERROR - the member would be told "fixed" while it is down. MAINTENANCE is
accepted (taken out of service, e.g. waiting for a part).
A simulated station (is_simulated, no hardware) has nobody to tap a card: the
technician's "fixed" in the app puts it back to READY (maintenance recorded,
open errors closed).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.charging.realtime import to_jsonable
from app.modules.logs.service import record_audit
from app.modules.notifications import events
from app.modules.station.service import get_station
from app.websocket.manager import manager

tickets = get_collection("support_tickets")
sessions = get_collection("charging_sessions")
error_logs = get_collection("error_logs")
maintenance_records = get_collection("maintenance_records")
users = get_collection("users")

OPEN_STATUSES = ("OPEN", "IN_PROGRESS")
STATION_STILL_IN_ERROR = (
    "Station is still in ERROR; put it in maintenance or back to READY at the station first"
)
# A session this recent at the same station is linked to the ticket.
RELATED_SESSION_WINDOW = timedelta(hours=6)


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _station_snapshot(station: dict) -> dict[str, Any]:
    open_errors = [
        e
        async for e in error_logs.find(
            {"station_id": station["station_id"], "resolved_at": None},
            {"_id": 0, "error_id": 1, "error_type": 1, "severity": 1, "message": 1, "occurred_at": 1},
        )
        .sort("occurred_at", -1)
        .limit(5)
    ]
    return to_jsonable(
        {
            "name": station.get("name"),
            "address": station.get("address"),
            "status": station.get("status"),
            "availability": station.get("availability"),
            "is_online": station.get("is_online"),
            "connector_lock": station.get("connector_lock"),
            "current_session_id": station.get("current_session_id"),
            "last_heartbeat_at": station.get("last_heartbeat_at"),
            "latest_telemetry": station.get("latest_telemetry"),
            "open_errors": open_errors,
            "captured_at": now_utc(),
        }
    )


async def _related_session_id(user_id: str, station_id: str) -> str | None:
    session = await sessions.find_one(
        {
            "user_id": user_id,
            "station_id": station_id,
            "started_at": {"$gte": now_utc() - RELATED_SESSION_WINDOW},
        },
        {"_id": 0, "session_id": 1},
        sort=[("started_at", -1)],
    )
    return session["session_id"] if session else None


async def _names(user_ids: list[str | None]) -> dict[str, dict]:
    ids = list({uid for uid in user_ids if uid})
    if not ids:
        return {}
    return {
        u["user_id"]: u
        async for u in users.find(
            {"user_id": {"$in": ids}},
            {"_id": 0, "user_id": 1, "name": 1, "phone": 1},
        )
    }


async def ticket_views(items: list[dict]) -> list[dict]:
    people = await _names([t["user_id"] for t in items] + [t.get("assigned_to") for t in items])
    views = []
    for t in items:
        member = people.get(t["user_id"], {})
        technician = people.get(t.get("assigned_to") or "", {})
        views.append(
            {
                **t,
                "station_name": t["station_snapshot"].get("name"),
                "category_label": events.category_label(t["category"]),
                "user_name": member.get("name"),
                "user_phone": member.get("phone"),
                "assigned_name": technician.get("name"),
            }
        )
    return views


async def _view(ticket: dict) -> dict:
    return (await ticket_views([ticket]))[0]


async def _broadcast(ticket: dict) -> None:
    # user_id scopes it to the member and the monitor roles (technicians, admins).
    await manager.broadcast(
        to_jsonable(
            {
                "type": "support_ticket_updated",
                "ticket_id": ticket["ticket_id"],
                "station_id": ticket["station_id"],
                "status": ticket["status"],
                "user_id": ticket["user_id"],
                "assigned_to": ticket.get("assigned_to"),
            }
        )
    )


async def create_ticket(member: dict, *, station_id: str, category: str, description: str) -> dict:
    station = await get_station(station_id)
    if not station:
        raise ResourceNotFoundError("Station not found")

    now = now_utc()
    ticket = {
        "ticket_id": f"TK-{uuid4().hex[:10].upper()}",
        "user_id": member["user_id"],
        "station_id": station_id,
        "category": category,
        "description": description.strip(),
        "status": "OPEN",
        "station_snapshot": await _station_snapshot(station),
        "session_id": await _related_session_id(member["user_id"], station_id),
        "assigned_to": None,
        "assigned_at": None,
        "resolution_note": None,
        "resolved_at": None,
        "cancelled_at": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await tickets.insert_one(ticket)
    except DuplicateKeyError as exc:
        raise ConflictError("You already have an open support request for this station") from exc
    ticket.pop("_id", None)

    snapshot = ticket["station_snapshot"]
    await record_audit(
        actor=member,
        action="SUPPORT_TICKET_CREATE",
        target_type="station",
        target_id=station_id,
        metadata={
            "ticket_id": ticket["ticket_id"],
            "category": category,
            "session_id": ticket["session_id"],
            "station_status": snapshot.get("status"),
            "is_online": snapshot.get("is_online"),
            "open_errors": [e["error_type"] for e in snapshot.get("open_errors", [])],
        },
    )
    await events.support_ticket_created(ticket, member, station.get("name"))
    await _broadcast(ticket)
    return await _view(ticket)


async def _get(ticket_id: str, query: dict | None = None) -> dict:
    ticket = await tickets.find_one({"ticket_id": ticket_id, **(query or {})}, {"_id": 0})
    if not ticket:
        raise ResourceNotFoundError("Support ticket not found")
    return ticket


async def list_tickets(*, query: dict, limit: int, offset: int) -> dict:
    items = await (
        tickets.find(query, {"_id": 0}).sort("created_at", -1).skip(offset).limit(limit)
    ).to_list(length=limit)
    return {
        "items": await ticket_views(items),
        "total": await tickets.count_documents(query),
        "limit": limit,
        "offset": offset,
    }


async def get_member_ticket(member: dict, ticket_id: str) -> dict:
    return await _view(await _get(ticket_id, {"user_id": member["user_id"]}))


async def get_ticket(ticket_id: str) -> dict:
    return await _view(await _get(ticket_id))


async def cancel_ticket(member: dict, ticket_id: str) -> dict:
    await _get(ticket_id, {"user_id": member["user_id"]})
    now = now_utc()
    updated = await tickets.find_one_and_update(
        {"ticket_id": ticket_id, "user_id": member["user_id"], "status": "OPEN"},
        {"$set": {"status": "CANCELLED", "cancelled_at": now, "updated_at": now}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise BusinessRuleError("Only an open request that no technician has taken can be cancelled")

    await record_audit(
        actor=member,
        action="SUPPORT_TICKET_CANCEL",
        target_type="support_ticket",
        target_id=ticket_id,
    )
    await _broadcast(updated)
    return await _view(updated)


async def accept_ticket(technician: dict, ticket_id: str) -> dict:
    ticket = await _get(ticket_id)
    if ticket["status"] == "IN_PROGRESS" and ticket.get("assigned_to") == technician["user_id"]:
        return await _view(ticket)

    now = now_utc()
    updated = await tickets.find_one_and_update(
        {"ticket_id": ticket_id, "status": "OPEN"},
        {
            "$set": {
                "status": "IN_PROGRESS",
                "assigned_to": technician["user_id"],
                "assigned_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise ConflictError("Request was already taken by another technician or closed")

    await record_audit(
        actor=technician,
        action="SUPPORT_TICKET_ACCEPT",
        target_type="support_ticket",
        target_id=ticket_id,
        metadata={"station_id": ticket["station_id"]},
    )
    await events.support_ticket_accepted(updated, technician)
    await _broadcast(updated)
    return await _view(updated)


async def _put_simulated_station_back(technician: dict, station: dict, note: str) -> None:
    """Maintenance by this technician, completed now; the station goes READY like the
    technician's card + STOP at a real station (errors closed, live update sent)."""
    # Imported here: admin.stations pulls in the charging / realtime modules.
    from app.modules.admin.stations import send_command

    station_id = station["station_id"]
    if not await maintenance_records.find_one({"station_id": station_id, "status": "IN_PROGRESS"}, {"_id": 1}):
        open_error = await error_logs.find_one(
            {"station_id": station_id, "resolved_at": None},
            {"_id": 0, "error_id": 1},
            sort=[("occurred_at", -1)],
        )
        now = now_utc()
        await maintenance_records.insert_one(
            {
                "maintenance_id": f"MR-{uuid4().hex[:8].upper()}",
                "station_id": station_id,
                "technician_id": technician["user_id"],
                "error_id": open_error["error_id"] if open_error else None,
                "status": "IN_PROGRESS",
                "description": f"Xác nhận đã sửa trên ứng dụng (trạm mô phỏng): {note}",
                "started_at": now,
                "completed_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    await send_command(station_id, "READY", f"Kỹ thuật viên {technician['name']} xác nhận đã sửa: {note}", technician)


async def resolve_ticket(technician: dict, ticket_id: str, note: str) -> dict:
    ticket = await _get(ticket_id)
    if ticket["status"] != "IN_PROGRESS" or ticket.get("assigned_to") != technician["user_id"]:
        raise BusinessRuleError("Accept the request before resolving it")
    station = await get_station(ticket["station_id"])
    simulated = bool(station and station.get("is_simulated"))
    if station and station.get("status") == "ERROR" and not simulated:
        raise BusinessRuleError(STATION_STILL_IN_ERROR)
    if simulated and station.get("status") in ("ERROR", "MAINTENANCE"):
        # Before closing the request: if the station cannot go back (open session), nothing changes.
        await _put_simulated_station_back(technician, station, note.strip())

    now = now_utc()
    updated = await tickets.find_one_and_update(
        {"ticket_id": ticket_id, "status": "IN_PROGRESS", "assigned_to": technician["user_id"]},
        {
            "$set": {
                "status": "RESOLVED",
                "resolution_note": note.strip(),
                "resolved_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise ConflictError("Request changed; reload and try again")

    await record_audit(
        actor=technician,
        action="SUPPORT_TICKET_RESOLVE",
        target_type="support_ticket",
        target_id=ticket_id,
        metadata={"station_id": ticket["station_id"], "note": note.strip()},
    )
    await events.support_ticket_resolved(updated, technician)
    await _broadcast(updated)
    return await _view(updated)
