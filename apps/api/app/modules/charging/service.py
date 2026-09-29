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
from app.modules.billing.wallet import create_member_hold
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


def now_utc() -> datetime:
    return datetime.now(UTC)


def normalize_stop_reason(stop_reason: str | StopReason) -> str:
    try:
        return StopReason(stop_reason).value
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

    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0},
    )
    if not station:
        raise ResourceNotFoundError("Station not found")

    if station.get("status") != StationStatus.READY.value:
        raise BusinessRuleError("Station is not READY")

    if not station.get("is_online", False):
        raise BusinessRuleError("Station is offline")

    open_station_session = await get_open_session_for_station(station_id)
    if open_station_session:
        raise ConflictError("Station already has an open session")

    current_user_session = await get_open_session_for_user(user_id)
    if current_user_session:
        raise ConflictError("User already has an open session")

    try:
        effective = effective_budget(
            int(user.get("balance_vnd", 0)),
            max_budget_vnd,
        )
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc

    if effective <= 0:
        raise BusinessRuleError("Insufficient balance")

    return user, effective


async def validate_guest_start(
    *,
    station_id: str,
    max_budget_vnd: int | None,
) -> int | None:
    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0},
    )
    if not station:
        raise ResourceNotFoundError("Station not found")

    if station.get("status") != StationStatus.READY.value:
        raise BusinessRuleError("Station is not READY")

    if not station.get("is_online", False):
        raise BusinessRuleError("Station is offline")

    open_station_session = await get_open_session_for_station(station_id)
    if open_station_session:
        raise ConflictError("Station already has an open session")

    if max_budget_vnd is not None and max_budget_vnd < 0:
        raise BusinessRuleError("max_budget_vnd must be >= 0")

    return max_budget_vnd


async def create_session_document(
    *,
    station_id: str,
    session_type: SessionType,
    user_id: str | None,
    max_budget_vnd: int | None,
) -> dict:
    if session_type == SessionType.MEMBER:
        if not user_id:
            raise BusinessRuleError("MEMBER session requires user_id")

        user = await users.find_one(
            {"user_id": user_id},
            {"_id": 0},
        )
        if not user:
            raise ResourceNotFoundError("User not found")

        if not user.get("is_active"):
            raise BusinessRuleError("User is inactive")

        if user.get("role") != "USER":
            raise BusinessRuleError("Only USER can create MEMBER session")

        try:
            effective = effective_budget(
                int(user.get("balance_vnd", 0)),
                max_budget_vnd,
            )
        except ValueError as exc:
            raise BusinessRuleError(str(exc)) from exc

        if effective <= 0:
            raise BusinessRuleError("Insufficient balance")

        max_budget_vnd = effective

    elif session_type == SessionType.GUEST:
        if user_id is not None:
            raise BusinessRuleError("GUEST session cannot have user_id")

        if max_budget_vnd is not None and max_budget_vnd < 0:
            raise BusinessRuleError("max_budget_vnd must be >= 0")

    else:
        raise BusinessRuleError("Unsupported session type")

    session_id = str(uuid4())
    now = now_utc()

    document = {
        "session_id": session_id,
        "station_id": station_id,
        "type": session_type.value,
        "user_id": user_id,
        "status": SessionStatus.ACTIVE.value,
        "max_budget_vnd": max_budget_vnd,
        "energy_kwh": Decimal128("0"),
        "power_kw": Decimal128(str(settings.energy_power_kw_decimal)),
        "price_per_kwh_vnd": settings.price_per_kwh_vnd,
        "cost_vnd": 0,
        "stop_reason": None,
        "started_at": now,
        "ended_at": None,
        "connector_locked": False,
        "created_at": now,
        "updated_at": now,
    }

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
                "updated_at": now,
            }
        },
    )

    if station_result.matched_count != 1:
        raise ConflictError("Station is not available for a new session")

    try:
        await sessions.insert_one(document)
    except DuplicateKeyError as exc:
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
        raise ConflictError("An open charging session already exists") from exc
    except Exception:
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

    document = await create_session_document(
        station_id=station_id,
        session_type=SessionType.MEMBER,
        user_id=user_id,
        max_budget_vnd=effective,
    )

    try:
        await create_member_hold(
            user_id=user_id,
            session_id=document["session_id"],
            amount_vnd=effective,
        )
    except Exception:
        await sessions.delete_one(
            {
                "session_id": document["session_id"],
                "status": SessionStatus.ACTIVE.value,
            }
        )
        await stations.update_one(
            {
                "station_id": station_id,
                "status": StationStatus.CHARGING.value,
                "current_session_id": document["session_id"],
            },
            {
                "$set": {
                    "status": StationStatus.READY.value,
                    "current_session_id": None,
                    "updated_at": now_utc(),
                }
            },
        )
        raise

    return document


async def create_guest_session(
    *,
    station_id: str,
    max_budget_vnd: int | None,
) -> dict:
    effective = await validate_guest_start(
        station_id=station_id,
        max_budget_vnd=max_budget_vnd,
    )

    return await create_session_document(
        station_id=station_id,
        session_type=SessionType.GUEST,
        user_id=None,
        max_budget_vnd=effective,
    )


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

    if (
        max_budget_vnd is not None
        and next_cost_vnd > max_budget_vnd
    ):
        raise BusinessRuleError("Charging tick would exceed max budget")

    return next_cost_vnd


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

    now = now_utc()

    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
            "$or": [
                {"max_budget_vnd": None},
                {"max_budget_vnd": {"$exists": False}},
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
                "updated_at": now,
            },
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if result is not None:
        return result

    session = await sessions.find_one(
        {"session_id": session_id},
        {
            "_id": 0,
            "status": 1,
            "cost_vnd": 1,
            "max_budget_vnd": 1,
        },
    )

    if not session:
        return None

    if session.get("status") != SessionStatus.ACTIVE.value:
        return None

    current_cost_vnd = int(session.get("cost_vnd", 0))
    max_budget_vnd = session.get("max_budget_vnd")

    if (
        max_budget_vnd is not None
        and current_cost_vnd + cost_delta_vnd > int(max_budget_vnd)
    ):
        raise BusinessRuleError("Charging tick would exceed max budget")

    return None


async def mark_session_waiting_payment(
    *,
    session_id: str,
    stop_reason: str | StopReason,
) -> dict:
    reason = normalize_stop_reason(stop_reason)

    if reason not in {
        StopReason.UNPLUG.value,
        StopReason.MAX_BUDGET.value,
        StopReason.INSUFFICIENT_BALANCE.value,
        StopReason.OVER_TEMPERATURE.value,
        StopReason.E_STOP.value,
        StopReason.SYSTEM_ERROR.value,
    }:
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
) -> dict:
    reason = normalize_stop_reason(stop_reason)
    now = now_utc()

    session = await sessions.find_one(
        {
            "session_id": session_id,
            "status": {
                "$in": [
                    SessionStatus.ACTIVE.value,
                    SessionStatus.WAITING_PAYMENT.value,
                ],
            },
        },
        {"_id": 0},
    )

    if not session:
        raise ResourceNotFoundError("Open session not found")

    ended_at = session.get("ended_at") or now

    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": {
                "$in": [
                    SessionStatus.ACTIVE.value,
                    SessionStatus.WAITING_PAYMENT.value,
                ],
            },
        },
        {
            "$set": {
                "status": SessionStatus.COMPLETED.value,
                "stop_reason": reason,
                "ended_at": ended_at,
                "connector_locked": False,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not result:
        raise ConflictError("Session was completed by another request")

    station_result = await stations.update_one(
        {
            "station_id": result["station_id"],
            "current_session_id": session_id,
        },
        {
            "$set": {
                "status": StationStatus.READY.value,
                "current_session_id": None,
                "updated_at": now,
            }
        },
    )

    if station_result.matched_count != 1:
        station = await stations.find_one(
            {"station_id": result["station_id"]},
            {"_id": 0, "status": 1, "current_session_id": 1},
        )
        if not (
            station
            and station.get("status") == StationStatus.READY.value
            and station.get("current_session_id") is None
        ):
            raise ConflictError("Station/session state could not be finalized")

    return result
