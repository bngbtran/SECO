from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from bson.decimal128 import Decimal128
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.billing.service import effective_budget
from app.modules.charging.constants import (
    SessionStatus,
    SessionType,
    StationStatus,
    StopReason,
)

settings = get_settings()

sessions = get_collection("charging_sessions")
stations = get_collection("charging_stations")
users = get_collection("users")

OPEN_SESSION_STATUSES = (
    SessionStatus.ACTIVE.value,
    SessionStatus.WAITING_PAYMENT.value,
)

_STOP_REASON_ALIASES = {
    "UNPLUGGED": StopReason.UNPLUG.value,
    "MAX_BUDGET_REACHED": StopReason.MAX_BUDGET.value,
    "INSUFFICIENT_FUNDS": StopReason.INSUFFICIENT_BALANCE.value,
    "OVERHEAT": StopReason.OVER_TEMPERATURE.value,
    "OVER_TEMP": StopReason.OVER_TEMPERATURE.value,
    "ESTOP": StopReason.E_STOP.value,
    "E-STOP": StopReason.E_STOP.value,
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def normalize_stop_reason(stop_reason: str | StopReason) -> str:
    if isinstance(stop_reason, StopReason):
        return stop_reason.value

    normalized = str(stop_reason).strip().upper()
    normalized = _STOP_REASON_ALIASES.get(normalized, normalized)

    try:
        return StopReason(normalized).value
    except ValueError as exc:
        raise BusinessRuleError(f"Invalid stop reason: {stop_reason}") from exc


async def get_open_session_for_station(station_id: str) -> dict | None:
    return await sessions.find_one(
        {
            "station_id": station_id,
            "status": {"$in": OPEN_SESSION_STATUSES},
        },
        {"_id": 0},
    )


async def get_open_session_for_user(user_id: str) -> dict | None:
    return await sessions.find_one(
        {
            "user_id": user_id,
            "status": {"$in": OPEN_SESSION_STATUSES},
        },
        {"_id": 0},
    )


async def _ensure_station_available(station_id: str) -> dict:
    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0},
    )
    if not station:
        raise ResourceNotFoundError("Station not found")

    if station.get("status") != StationStatus.READY.value:
        raise ConflictError("Station is not READY")

    if not station.get("is_online", False):
        raise ConflictError("Station is offline")

    if station.get("current_session_id") or await get_open_session_for_station(station_id):
        raise ConflictError("Station already has an open session")

    return station


def resolve_member_budget(
    balance_vnd: int,
    max_budget_vnd: int | None,
) -> int:
    """Return the budget the session is capped at (DD 3.1).

    No explicit budget means the member can charge up to the whole balance.
    """
    if max_budget_vnd is not None and max_budget_vnd <= 0:
        raise BusinessRuleError("max_budget_vnd must be greater than 0")

    try:
        effective = effective_budget(balance_vnd, max_budget_vnd)
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc

    if effective <= 0:
        raise BusinessRuleError("Insufficient balance")

    return effective


async def validate_member_start(
    *,
    user_id: str,
    station_id: str,
    max_budget_vnd: int | None,
) -> tuple[dict, int]:
    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0},
    )
    if not user:
        raise ResourceNotFoundError("User not found")

    if not user.get("is_active"):
        raise BusinessRuleError("User is inactive")

    if user.get("role") != "USER":
        raise BusinessRuleError("Only USER can start member charging")

    effective = resolve_member_budget(
        int(user.get("balance_vnd", 0)),
        max_budget_vnd,
    )

    await _ensure_station_available(station_id)

    if await get_open_session_for_user(user_id):
        raise ConflictError("User already has an open session")

    return user, effective


async def validate_guest_start(
    *,
    station_id: str,
    max_budget_vnd: int | None,
) -> int | None:
    if max_budget_vnd is not None and max_budget_vnd <= 0:
        raise BusinessRuleError("max_budget_vnd must be greater than 0")

    await _ensure_station_available(station_id)

    return max_budget_vnd


def build_session_document(
    *,
    station_id: str,
    session_type: SessionType,
    user_id: str | None,
    max_budget_vnd: int | None,
) -> dict:
    now = now_utc()

    return {
        "session_id": str(uuid4()),
        "station_id": station_id,
        "type": session_type.value,
        "user_id": user_id,
        "status": SessionStatus.ACTIVE.value,
        "max_budget_vnd": max_budget_vnd,
        "energy_kwh": Decimal128("0"),
        "power_kw": Decimal128(str(settings.energy_power_kw_decimal)),
        # Snapshot so history does not change when the tariff changes (DD 5).
        "price_per_kwh_vnd": settings.price_per_kwh_vnd,
        "cost_vnd": 0,
        "stop_reason": None,
        "started_at": now,
        "ended_at": None,
        "connector_locked": False,
        "created_at": now,
        "updated_at": now,
    }


async def _release_station(station_id: str, session_id: str) -> None:
    await stations.update_one(
        {
            "station_id": station_id,
            "status": StationStatus.CHARGING.value,
            "current_session_id": session_id,
        },
        {
            "$set": {
                "status": StationStatus.READY.value,
                "current_session_id": None,
                "updated_at": now_utc(),
            }
        },
    )


async def insert_session(document: dict) -> dict:
    station_id = document["station_id"]
    session_id = document["session_id"]

    # Claim the station first so two concurrent starts cannot both win.
    station_result = await stations.update_one(
        {
            "station_id": station_id,
            "status": StationStatus.READY.value,
            "current_session_id": None,
            "is_online": True,
        },
        {
            "$set": {
                "status": StationStatus.CHARGING.value,
                "current_session_id": session_id,
                "updated_at": now_utc(),
            }
        },
    )

    if station_result.matched_count != 1:
        raise ConflictError("Station is not available for a new session")

    try:
        await sessions.insert_one(document)
    except DuplicateKeyError as exc:
        await _release_station(station_id, session_id)
        raise ConflictError("An open charging session already exists") from exc
    except Exception:
        await _release_station(station_id, session_id)
        raise

    return document


async def create_member_session(
    *,
    user_id: str,
    station_id: str,
    max_budget_vnd: int | None,
) -> dict:
    _, effective = await validate_member_start(
        user_id=user_id,
        station_id=station_id,
        max_budget_vnd=max_budget_vnd,
    )

    document = build_session_document(
        station_id=station_id,
        session_type=SessionType.MEMBER,
        user_id=user_id,
        max_budget_vnd=effective,
    )
    document["budget_mode"] = "EXPLICIT" if max_budget_vnd is not None else "BALANCE"

    return await insert_session(document)


async def create_guest_session(
    *,
    station_id: str,
    max_budget_vnd: int | None,
) -> dict:
    effective = await validate_guest_start(
        station_id=station_id,
        max_budget_vnd=max_budget_vnd,
    )

    document = build_session_document(
        station_id=station_id,
        session_type=SessionType.GUEST,
        user_id=None,
        max_budget_vnd=effective,
    )

    return await insert_session(document)


def ensure_tick_within_budget(
    current_cost_vnd: int,
    cost_delta_vnd: int,
    max_budget_vnd: int | None,
) -> int:
    if current_cost_vnd < 0:
        raise ValueError("current_cost_vnd must be >= 0")

    if cost_delta_vnd < 0:
        raise ValueError("cost_delta_vnd must be >= 0")

    if max_budget_vnd is not None and max_budget_vnd < 0:
        raise ValueError("max_budget_vnd must be >= 0")

    next_cost_vnd = current_cost_vnd + cost_delta_vnd

    if max_budget_vnd is not None and next_cost_vnd > max_budget_vnd:
        raise BusinessRuleError("Charging tick would exceed max budget")

    return next_cost_vnd


async def set_session_usage(
    *,
    session_id: str,
    energy_kwh: Decimal,
    cost_vnd: int,
) -> dict | None:
    """Overwrite the accumulated usage of an ACTIVE session (idempotent)."""
    if energy_kwh < 0:
        raise ValueError("energy_kwh must be >= 0")

    if cost_vnd < 0:
        raise ValueError("cost_vnd must be >= 0")

    return await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
        },
        {
            "$set": {
                "energy_kwh": Decimal128(str(energy_kwh)),
                "cost_vnd": cost_vnd,
                "updated_at": now_utc(),
            },
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def add_energy_tick(
    *,
    session_id: str,
    energy_delta_kwh: Decimal,
    cost_delta_vnd: int,
) -> dict | None:
    if energy_delta_kwh < 0:
        raise ValueError("energy_delta_kwh must be >= 0")

    if cost_delta_vnd < 0:
        raise ValueError("cost_delta_vnd must be >= 0")

    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
            "$or": [
                {"max_budget_vnd": None},
                {
                    "$expr": {
                        "$lte": [
                            {"$add": ["$cost_vnd", cost_delta_vnd]},
                            "$max_budget_vnd",
                        ]
                    }
                },
            ],
        },
        {
            "$inc": {
                "energy_kwh": Decimal128(str(energy_delta_kwh)),
                "cost_vnd": cost_delta_vnd,
            },
            "$set": {
                "updated_at": now_utc(),
            },
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if result is not None:
        return result

    session = await sessions.find_one(
        {"session_id": session_id},
        {"_id": 0, "status": 1, "cost_vnd": 1, "max_budget_vnd": 1},
    )

    if not session or session.get("status") != SessionStatus.ACTIVE.value:
        return None

    ensure_tick_within_budget(
        int(session.get("cost_vnd", 0)),
        cost_delta_vnd,
        session.get("max_budget_vnd"),
    )
    return None


async def mark_session_waiting_payment(
    *,
    session_id: str,
    stop_reason: str | StopReason,
) -> dict:
    reason = normalize_stop_reason(stop_reason)

    if reason == StopReason.TECHNICIAN.value:
        raise BusinessRuleError(
            f"Stop reason {reason} cannot move session to WAITING_PAYMENT"
        )

    now = now_utc()

    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
            "type": SessionType.GUEST.value,
        },
        {
            "$set": {
                "status": SessionStatus.WAITING_PAYMENT.value,
                "stop_reason": reason,
                "connector_locked": True,
                "ended_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not result:
        raise ResourceNotFoundError("Active guest session not found")

    return result


async def complete_session(
    *,
    session_id: str,
    stop_reason: str | StopReason,
    final_station_status: str | StationStatus = StationStatus.READY,
) -> dict:
    reason = normalize_stop_reason(stop_reason)
    station_status = StationStatus(final_station_status).value
    now = now_utc()

    session = await sessions.find_one(
        {"session_id": session_id},
        {"_id": 0},
    )

    if not session:
        raise ResourceNotFoundError("Charging session not found")

    if session.get("status") == SessionStatus.COMPLETED.value:
        return session

    if session.get("status") not in OPEN_SESSION_STATUSES:
        raise BusinessRuleError(
            f"Cannot complete session in status {session.get('status')}"
        )

    # A guest session keeps the stop reason recorded when it started waiting for payment.
    keep_reason = session.get("status") == SessionStatus.WAITING_PAYMENT.value

    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": {"$in": OPEN_SESSION_STATUSES},
        },
        {
            "$set": {
                "status": SessionStatus.COMPLETED.value,
                "stop_reason": session.get("stop_reason") if keep_reason else reason,
                "ended_at": session.get("ended_at") or now,
                "connector_locked": False,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not result:
        # Unplug, budget stop and the ticker can race; the first one wins.
        latest = await sessions.find_one({"session_id": session_id}, {"_id": 0})
        if latest and latest.get("status") == SessionStatus.COMPLETED.value:
            return latest
        raise ConflictError("Session was completed by another request")

    await stations.update_one(
        {
            "station_id": result["station_id"],
            "current_session_id": session_id,
        },
        {
            "$set": {
                "status": station_status,
                "current_session_id": None,
                "updated_at": now,
            }
        },
    )

    return result
