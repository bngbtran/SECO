"""UC-A03 CRUD stations, UC-A08 live station view, UC-A14 remote commands."""

import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Response, status
from pymongo.errors import DuplicateKeyError

from app.core.dependencies import CurrentAdmin
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.admin.common import Limit, Offset, http_errors, page
from app.modules.admin.schemas import (
    AdminLiveStation,
    AdminStation,
    AdminStationCommandRequest,
    AdminStationCommandResponse,
    AdminStationCreate,
    AdminStationDetail,
    AdminStationUpdate,
    Page,
)
from app.modules.admin.views import (
    error_views,
    maintenance_views,
    session_views,
    station_view,
)
from app.modules.charging.constants import (
    ConnectorLock,
    SessionStatus,
    SessionType,
    StationStatus,
    StopReason,
)
from app.modules.charging.realtime import publish_station_command
from app.modules.charging.usage import refresh_session_usage
from app.modules.guest.realtime import announce_guest_stopped
from app.modules.guest.service import stop_guest_session
from app.modules.logs.service import record_audit
from app.modules.member.realtime import announce_stopped
from app.modules.member.service import stop_member_session
from app.modules.station.service import StationAvailability
from app.websocket.manager import manager

router = APIRouter(tags=["Admin · Stations"])

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
maintenance_records = get_collection("maintenance_records")
error_logs = get_collection("error_logs")

_OPEN = [SessionStatus.ACTIVE.value, SessionStatus.WAITING_PAYMENT.value]


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _get_station(station_id: str) -> dict:
    station = await stations.find_one({"station_id": station_id}, {"_id": 0})
    if not station:
        raise ResourceNotFoundError("Station not found")
    return station


async def _session_statuses(items: list[dict]) -> dict[str, str]:
    ids = [s["current_session_id"] for s in items if s.get("current_session_id")]
    if not ids:
        return {}
    return {
        s["session_id"]: s["status"]
        async for s in sessions.find({"session_id": {"$in": ids}}, {"_id": 0, "status": 1, "session_id": 1})
    }


async def _station_views(items: list[dict]) -> list[dict]:
    statuses = await _session_statuses(items)
    return [station_view(s, statuses.get(s.get("current_session_id"))) for s in items]


async def station_out(station_id: str) -> dict:
    return (await _station_views([await _get_station(station_id)]))[0]


async def _broadcast_station(station: dict) -> None:
    await manager.broadcast(
        {
            "type": "station_updated",
            "station_id": station["station_id"],
            "status": station["status"],
            "availability": station["availability"],
            "is_online": station["is_online"],
            "connector_lock": station["connector_lock"],
            "current_session_id": station.get("current_session_id"),
        }
    )


# ---------------------------------------------------------------------------
# CRUD (UC-A03)
# ---------------------------------------------------------------------------


async def list_stations(
    *,
    q: str | None,
    availability: str | None,
    is_simulated: bool | None,
    limit: int,
    offset: int,
) -> dict:
    query: dict = {}
    if q:
        pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"station_id": pattern}, {"name": pattern}, {"address": pattern}]
    if is_simulated is not None:
        query["is_simulated"] = True if is_simulated else {"$ne": True}

    items = await _station_views(
        await stations.find(query, {"_id": 0}).sort("station_id", 1).to_list(length=None)
    )
    # Availability is derived, so it is filtered after loading (a few dozen stations).
    if availability:
        items = [s for s in items if s["availability"] == availability]

    return page(items[offset : offset + limit], len(items), limit, offset)


async def get_station_detail(station_id: str) -> dict:
    station = await station_out(station_id)

    current = None
    if station.get("current_session_id"):
        current = await sessions.find_one({"session_id": station["current_session_id"]}, {"_id": 0})
        if current and current["status"] == SessionStatus.ACTIVE.value:
            current, _ = await refresh_session_usage(current)

    recent = await sessions.find({"station_id": station_id}, {"_id": 0}).sort(
        "started_at", -1
    ).limit(10).to_list(length=10)
    open_errors = await error_logs.find(
        {"station_id": station_id, "resolved_at": None},
        {"_id": 0},
    ).sort("occurred_at", -1).to_list(length=20)
    maintenance = await maintenance_records.find({"station_id": station_id}, {"_id": 0}).sort(
        "started_at", -1
    ).limit(10).to_list(length=10)

    return {
        **station,
        "current_session": (await session_views([current]))[0] if current else None,
        "recent_sessions": await session_views(recent),
        "open_errors": await error_views(open_errors),
        "maintenance": await maintenance_views(maintenance),
    }


async def create_station(data: AdminStationCreate, actor: dict) -> dict:
    now = now_utc()
    station = {
        "station_id": data.station_id,
        "name": data.name.strip(),
        "address": data.address,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "is_simulated": data.is_simulated,
        "status": StationStatus.READY.value,
        # A real station is offline until its ESP32 sends a heartbeat; a mock is always up.
        "is_online": data.is_simulated,
        "connector_lock": ConnectorLock.UNLOCKED.value,
        "current_session_id": None,
        "last_heartbeat_at": now if data.is_simulated else None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await stations.insert_one(station)
    except DuplicateKeyError as exc:
        raise ConflictError("Station ID already exists") from exc

    await record_audit(
        actor=actor,
        action="STATION_CREATE",
        target_type="station",
        target_id=data.station_id,
        metadata={"name": station["name"], "is_simulated": data.is_simulated},
    )
    result = await station_out(data.station_id)
    await _broadcast_station(result)
    return result


async def update_station(station_id: str, data: AdminStationUpdate, actor: dict) -> dict:
    await _get_station(station_id)
    changes = data.model_dump(exclude_unset=True)
    if changes:
        await stations.update_one(
            {"station_id": station_id},
            {"$set": {**changes, "updated_at": now_utc()}},
        )
        await record_audit(
            actor=actor,
            action="STATION_UPDATE",
            target_type="station",
            target_id=station_id,
            metadata=changes,
        )
    return await station_out(station_id)


async def delete_station(station_id: str, actor: dict) -> None:
    station = await _get_station(station_id)

    if station.get("current_session_id") or await sessions.find_one(
        {"station_id": station_id, "status": {"$in": _OPEN}},
        {"_id": 1},
    ):
        raise ConflictError("Station has an open session")
    if await sessions.find_one({"station_id": station_id}, {"_id": 1}):
        raise ConflictError("Station has charging history; put it in MAINTENANCE instead")

    await stations.delete_one({"station_id": station_id})
    await record_audit(
        actor=actor,
        action="STATION_DELETE",
        target_type="station",
        target_id=station_id,
        metadata={"name": station["name"]},
    )
    await manager.broadcast({"type": "station_deleted", "station_id": station_id})


# ---------------------------------------------------------------------------
# Live view (UC-A08)
# ---------------------------------------------------------------------------


async def live_stations() -> list[dict]:
    items = await _station_views(
        await stations.find({}, {"_id": 0}).sort("station_id", 1).to_list(length=None)
    )
    current_ids = [s["current_session_id"] for s in items if s.get("current_session_id")]
    current = await sessions.find({"session_id": {"$in": current_ids}}, {"_id": 0}).to_list(
        length=None
    )
    refreshed = []
    for session in current:
        if session["status"] == SessionStatus.ACTIVE.value:
            session, _ = await refresh_session_usage(session)
        refreshed.append(session)
    views = {s["session_id"]: s for s in await session_views(refreshed)}

    return [{**s, "current_session": views.get(s.get("current_session_id"))} for s in items]


# ---------------------------------------------------------------------------
# Remote commands (UC-A14)
# ---------------------------------------------------------------------------


async def _stop_active_session(
    session: dict,
    station_status: StationStatus | None,
) -> dict:
    """Stop by an operator (stop_reason TECHNICIAN); the relay command is sent separately."""
    if session["type"] == SessionType.GUEST.value:
        result = await stop_guest_session(
            session_id=session["session_id"],
            stop_reason=StopReason.TECHNICIAN,
            station_status=station_status,
        )
        await announce_guest_stopped(result, send_stop=False)
    else:
        result = await stop_member_session(
            session_id=session["session_id"],
            stop_reason=StopReason.TECHNICIAN,
            station_status=station_status or StationStatus.READY,
        )
        await announce_stopped(result, send_stop=False)
    return result["session"]


async def _set_station_status(station_id: str, new_status: StationStatus) -> None:
    await stations.update_one(
        {"station_id": station_id},
        {"$set": {"status": new_status.value, "updated_at": now_utc()}},
    )


async def send_command(
    station_id: str,
    command: str,
    note: str | None,
    actor: dict,
) -> dict:
    station = await _get_station(station_id)
    active = await sessions.find_one(
        {"station_id": station_id, "status": SessionStatus.ACTIVE.value},
        {"_id": 0},
    )
    stopped = None
    now = now_utc()

    if command == "STOP":
        if not active:
            raise BusinessRuleError("Station has no active session")
        stopped = await _stop_active_session(active, None)

    elif command == "MAINTENANCE":
        if station["status"] == StationStatus.MAINTENANCE.value:
            raise BusinessRuleError("Station is already in MAINTENANCE")
        if active:
            stopped = await _stop_active_session(active, StationStatus.MAINTENANCE)
        await _set_station_status(station_id, StationStatus.MAINTENANCE)

        open_error = await error_logs.find_one(
            {"station_id": station_id, "resolved_at": None},
            {"_id": 0, "error_id": 1},
            sort=[("occurred_at", -1)],
        )
        if not await maintenance_records.find_one(
            {"station_id": station_id, "status": "IN_PROGRESS"},
            {"_id": 1},
        ):
            await maintenance_records.insert_one(
                {
                    "maintenance_id": f"MR-{uuid4().hex[:8].upper()}",
                    "station_id": station_id,
                    "technician_id": actor["user_id"],
                    "error_id": open_error["error_id"] if open_error else None,
                    "status": "IN_PROGRESS",
                    "description": note or "Admin chuyển trạm sang bảo trì từ xa",
                    "started_at": now,
                    "completed_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    elif command == "READY":
        if station["status"] not in (StationStatus.MAINTENANCE.value, StationStatus.ERROR.value):
            raise BusinessRuleError("Only a station in MAINTENANCE or ERROR can be set READY")
        if await sessions.find_one({"station_id": station_id, "status": {"$in": _OPEN}}, {"_id": 1}):
            raise ConflictError("Station still has an open session")
        await stations.update_one(
            {"station_id": station_id},
            {"$set": {"status": StationStatus.READY.value, "current_session_id": None, "updated_at": now}},
        )
        await maintenance_records.update_many(
            {"station_id": station_id, "status": "IN_PROGRESS"},
            {"$set": {"status": "COMPLETED", "completed_at": now, "updated_at": now}},
        )
        await error_logs.update_many(
            {"station_id": station_id, "resolved_at": None},
            {"$set": {"resolved_at": now, "resolved_by": actor["user_id"]}},
        )

    elif command == "RESET":
        # The ESP32 reboots with the relay OFF, which ends any charging.
        if active:
            stopped = await _stop_active_session(active, None)

    # LOCK / UNLOCK / PING only go to the station; the lock state follows its cmd_ack.

    request_id = str(uuid4())
    payload = {"command": command, "request_id": request_id}
    if command == "STOP" or (stopped and command in ("MAINTENANCE", "RESET")):
        payload["reason"] = StopReason.TECHNICIAN.value
    if stopped:
        payload["session_id"] = stopped["session_id"]
    published = await publish_station_command(station_id, payload)

    await record_audit(
        actor=actor,
        action="STATION_COMMAND",
        target_type="station",
        target_id=station_id,
        metadata={
            "command": command,
            "request_id": request_id,
            "note": note,
            "published": published,
            "stopped_session_id": stopped["session_id"] if stopped else None,
        },
    )

    result = await station_out(station_id)
    await _broadcast_station(result)

    return {
        "station_id": station_id,
        "command": command,
        "request_id": request_id,
        "published": published,
        "stopped_session": (await session_views([stopped]))[0] if stopped else None,
        "station": result,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/stations", response_model=Page[AdminStation])
async def get_stations(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    q: str | None = None,
    availability: StationAvailability | None = None,
    is_simulated: bool | None = None,
):
    return await list_stations(
        q=q,
        availability=availability,
        is_simulated=is_simulated,
        limit=limit,
        offset=offset,
    )


@router.post("/stations", response_model=AdminStation, status_code=status.HTTP_201_CREATED)
async def post_station(data: AdminStationCreate, admin: CurrentAdmin):
    with http_errors():
        return await create_station(data, admin)


@router.get("/monitor/stations", response_model=list[AdminLiveStation])
async def get_live_stations(admin: CurrentAdmin):
    """Snapshot for the live board; subscribe to /ws?token=... for updates."""
    return await live_stations()


@router.get("/stations/{station_id}", response_model=AdminStationDetail)
async def get_station(station_id: str, admin: CurrentAdmin):
    with http_errors():
        return await get_station_detail(station_id)


@router.patch("/stations/{station_id}", response_model=AdminStation)
async def patch_station(station_id: str, data: AdminStationUpdate, admin: CurrentAdmin):
    with http_errors():
        return await update_station(station_id, data, admin)


@router.delete("/stations/{station_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_station(station_id: str, admin: CurrentAdmin):
    with http_errors():
        await delete_station(station_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stations/{station_id}/commands", response_model=AdminStationCommandResponse)
async def post_station_command(
    station_id: str,
    data: AdminStationCommandRequest,
    admin: CurrentAdmin,
):
    with http_errors():
        return await send_command(station_id, data.command, data.note, admin)
