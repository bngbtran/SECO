from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from bson.decimal128 import Decimal128
from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.billing.service import effective_budget
from app.modules.charging.constants import SessionStatus, SessionType, StationStatus


settings = get_settings()
sessions = get_collection("charging_sessions")
stations = get_collection("charging_stations")
users = get_collection("users")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def get_open_session_for_station(station_id: str) -> dict | None:
    return await sessions.find_one(
        {
            "station_id": station_id,
            "status": {"$in": [SessionStatus.ACTIVE.value, SessionStatus.WAITING_PAYMENT.value]},
        },
        {"_id": 0},
    )


async def validate_member_start(
    *,
    user_id: str,
    station_id: str,
    max_budget_vnd: int | None,
) -> tuple[dict, int]:
    user = await users.find_one({"user_id": user_id}, {"_id": 0})
    if not user:
        raise ResourceNotFoundError("User not found")
    if not user.get("is_active"):
        raise BusinessRuleError("User is inactive")
    if user.get("role") != "USER":
        raise BusinessRuleError("Only USER can start member charging")

    station = await stations.find_one({"station_id": station_id}, {"_id": 0})
    if not station:
        raise ResourceNotFoundError("Station not found")
    if station.get("status") != StationStatus.READY.value:
        raise BusinessRuleError("Station is not READY")
    if not station.get("is_online", False):
        raise BusinessRuleError("Station is offline")

    open_station_session = await get_open_session_for_station(station_id)
    if open_station_session:
        raise ConflictError("Station already has an open session")

    current_user_session = await sessions.find_one(
        {
            "user_id": user_id,
            "status": {"$in": [SessionStatus.ACTIVE.value, SessionStatus.WAITING_PAYMENT.value]},
        },
        {"_id": 0},
    )
    if current_user_session:
        raise ConflictError("User already has an open session")

    try:
        effective = effective_budget(
            user["balance_vnd"],
            max_budget_vnd,
        )
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc

    if effective <= 0:
        raise BusinessRuleError("Insufficient balance")

    return user, effective


async def create_session_document(
    *,
    station_id: str,
    session_type: SessionType,
    user_id: str | None,
    max_budget_vnd: int | None,
) -> dict:
    now = now_utc()
    session_id = str(uuid4())

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

    await sessions.insert_one(document)
    station_result = await stations.update_one(
        {"station_id": station_id, "status": StationStatus.READY.value, "current_session_id": None},
        {
            "$set": {
                "status": StationStatus.CHARGING.value,
                "current_session_id": session_id,
                "updated_at": now,
            }
        },
    )
    if station_result.matched_count != 1:
        await sessions.delete_one({"session_id": session_id})
        raise ConflictError("Station changed state before session could start")

    return document


async def add_energy_tick(
    *,
    session_id: str,
    energy_delta_kwh: Decimal,
    cost_delta_vnd: int,
) -> dict | None:
    if energy_delta_kwh < 0 or cost_delta_vnd < 0:
        raise ValueError("tick values must be >= 0")

    now = now_utc()
    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
        },
        {
            "$inc": {
                "energy_kwh": Decimal128(str(energy_delta_kwh)),
                "cost_vnd": cost_delta_vnd,
            },
            "$set": {"updated_at": now},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return result


async def mark_session_waiting_payment(
    *,
    session_id: str,
    stop_reason: str,
) -> dict:
    now = now_utc()
    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
        },
        {
            "$set": {
                "status": SessionStatus.WAITING_PAYMENT.value,
                "stop_reason": stop_reason,
                "connector_locked": True,
                "ended_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise ResourceNotFoundError("Active session not found")
    return result


async def complete_session(
    *,
    session_id: str,
    stop_reason: str,
) -> dict:
    now = now_utc()
    result = await sessions.find_one_and_update(
        {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE.value,
        },
        {
            "$set": {
                "status": SessionStatus.COMPLETED.value,
                "stop_reason": stop_reason,
                "ended_at": now,
                "connector_locked": False,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise ResourceNotFoundError("Active session not found")

    await stations.update_one(
        {"station_id": result["station_id"], "current_session_id": session_id},
        {
            "$set": {
                "status": StationStatus.READY.value,
                "current_session_id": None,
                "updated_at": now,
            }
        },
    )
    return result
