from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.core.mongo_types import from_decimal128
from app.db.mongodb import get_collection
from app.modules.billing.service import cost_for_energy, energy_for_seconds, tick_values
from app.modules.billing.settlement import get_transaction_for_session, settle_member_session
from app.modules.charging.constants import (
    SessionStatus,
    SessionType,
    StationStatus,
    StopReason,
)
from app.modules.charging.service import (
    complete_session,
    create_member_session,
    get_open_session_for_user,
    resolve_member_budget,
    set_session_usage,
)

settings = get_settings()

rfid_cards = get_collection("rfid_cards")
users = get_collection("users")
stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
transactions = get_collection("transactions")

# How long a budget chosen in the mobile app waits for the RFID scan at the station.
PENDING_BUDGET_TTL = timedelta(minutes=10)


def now_utc() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    # PyMongo returns naive UTC datetimes unless tz_aware is enabled.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def serialize_session(session: dict) -> dict:
    result = dict(session)
    for key in ("energy_kwh", "power_kw"):
        if result.get(key) is not None:
            result[key] = from_decimal128(result[key])
    return result


async def verify_member_rfid(
    *,
    uid: str,
) -> tuple[dict, dict]:
    card = await rfid_cards.find_one(
        {
            "uid": uid.strip(),
            "is_active": True,
            "pending": False,
        },
        {"_id": 0},
    )

    if not card:
        raise ResourceNotFoundError("RFID card not found or inactive")

    user_id = card.get("user_id")
    if not user_id:
        raise BusinessRuleError("RFID card is not linked to a user")

    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password_hash": 0},
    )

    if not user:
        raise ResourceNotFoundError("RFID card owner not found")

    if not user.get("is_active"):
        raise BusinessRuleError("RFID card owner is inactive")

    if user.get("role") != "USER":
        raise BusinessRuleError("RFID card owner is not a USER")

    return card, user


def get_active_pending_budget(user: dict, station_id: str | None = None) -> dict | None:
    pending = user.get("pending_charging_budget")
    if not pending:
        return None

    if _as_utc(pending["expires_at"]) <= now_utc():
        return None

    if station_id is not None and pending.get("station_id") != station_id:
        return None

    return pending


async def set_pending_budget(
    *,
    user_id: str,
    station_id: str,
    max_budget_vnd: int,
) -> dict:
    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0, "balance_vnd": 1, "is_active": 1, "role": 1},
    )
    if not user:
        raise ResourceNotFoundError("Member user not found")

    if user.get("role") != "USER":
        raise BusinessRuleError("Only USER can set a charging budget")

    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0, "status": 1},
    )
    if not station:
        raise ResourceNotFoundError("Station not found")

    if station.get("status") != StationStatus.READY.value:
        raise ConflictError("Station is not READY")

    if await get_open_session_for_user(user_id):
        raise ConflictError("User already has an open session")

    balance_vnd = int(user.get("balance_vnd", 0))
    resolve_member_budget(balance_vnd, max_budget_vnd)

    now = now_utc()
    pending = {
        "station_id": station_id,
        "max_budget_vnd": max_budget_vnd,
        "created_at": now,
        "expires_at": now + PENDING_BUDGET_TTL,
    }

    await users.update_one(
        {"user_id": user_id},
        {"$set": {"pending_charging_budget": pending, "updated_at": now}},
    )

    return {
        "user_id": user_id,
        "balance_vnd": balance_vnd,
        **pending,
    }


async def get_pending_budget(*, user_id: str) -> dict | None:
    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0, "balance_vnd": 1, "pending_charging_budget": 1},
    )
    if not user:
        raise ResourceNotFoundError("Member user not found")

    pending = get_active_pending_budget(user)
    if not pending:
        return None

    return {
        "user_id": user_id,
        "balance_vnd": int(user.get("balance_vnd", 0)),
        **pending,
    }


async def clear_pending_budget(*, user_id: str) -> None:
    await users.update_one(
        {"user_id": user_id},
        {"$unset": {"pending_charging_budget": ""}},
    )


async def start_member_charging(
    *,
    uid: str,
    station_id: str,
    max_budget_vnd: int | None,
    expected_user_id: str | None = None,
) -> dict:
    _, user = await verify_member_rfid(uid=uid)

    if expected_user_id is not None and user["user_id"] != expected_user_id:
        raise BusinessRuleError("RFID card does not belong to current user")

    # An explicit budget from the station wins; otherwise use the one set in the app.
    if max_budget_vnd is None:
        pending = get_active_pending_budget(user, station_id)
        if pending:
            max_budget_vnd = int(pending["max_budget_vnd"])

    session = await create_member_session(
        user_id=user["user_id"],
        station_id=station_id,
        max_budget_vnd=max_budget_vnd,
    )

    if user.get("pending_charging_budget"):
        await clear_pending_budget(user_id=user["user_id"])

    return {
        "session": session,
        "user": user,
    }


async def get_member_session(
    *,
    user_id: str,
    session_id: str,
) -> dict:
    session = await sessions.find_one(
        {
            "session_id": session_id,
            "user_id": user_id,
            "type": SessionType.MEMBER.value,
        },
        {"_id": 0},
    )

    if not session:
        raise ResourceNotFoundError("Member charging session not found")

    return session


def _budget_stop_reason(session: dict) -> str:
    if session.get("budget_mode") == "EXPLICIT":
        return StopReason.MAX_BUDGET.value
    return StopReason.INSUFFICIENT_BALANCE.value


async def refresh_session_usage(session: dict) -> tuple[dict, bool]:
    """Recompute usage of an ACTIVE member session from elapsed time.

    Energy is simulated (DD 5): 0.15 kW every 30 s tick, so usage depends only on
    how many whole ticks have passed since the session started. This keeps the
    update idempotent no matter how often telemetry or the ticker call it.
    Returns the (possibly updated) session and whether the next tick would
    exceed the budget.
    """
    tick_seconds = settings.energy_tick_seconds
    price = int(session.get("price_per_kwh_vnd", settings.price_per_kwh_vnd))
    power_kw = from_decimal128(session.get("power_kw", settings.energy_power_kw_decimal))
    _, tick_cost_vnd = tick_values()

    elapsed = (now_utc() - _as_utc(session["started_at"])).total_seconds()
    ticks = max(int(elapsed // tick_seconds), 0)

    budget = session.get("max_budget_vnd")
    if budget is not None and tick_cost_vnd > 0:
        ticks = min(ticks, int(budget) // tick_cost_vnd)

    energy_kwh = energy_for_seconds(ticks * tick_seconds, power_kw)
    cost_vnd = cost_for_energy(energy_kwh, price)

    current_energy = from_decimal128(session.get("energy_kwh", Decimal(0)))
    if energy_kwh > current_energy:
        updated = await set_session_usage(
            session_id=session["session_id"],
            energy_kwh=energy_kwh,
            cost_vnd=cost_vnd,
        )
        session = updated or session

    budget_exhausted = budget is not None and cost_vnd + tick_cost_vnd > int(budget)
    return session, budget_exhausted


async def stop_member_session(
    *,
    session_id: str,
    stop_reason: str | StopReason,
    station_status: str | StationStatus = StationStatus.READY,
) -> dict:
    """Stop a member session and deduct its final cost from the wallet."""
    active = await sessions.find_one(
        {"session_id": session_id, "status": SessionStatus.ACTIVE.value},
        {"_id": 0},
    )
    if active:
        # Bill every whole tick up to the moment of stopping.
        await refresh_session_usage(active)

    session = await complete_session(
        session_id=session_id,
        stop_reason=stop_reason,
        final_station_status=station_status,
    )

    transaction = await settle_member_session(session_id=session_id)

    return {
        "session": session,
        "transaction": transaction,
    }


async def stop_member_charging(
    *,
    user_id: str,
    session_id: str,
    stop_reason: str | StopReason = StopReason.UNPLUG,
) -> tuple[dict, dict | None]:
    session = await get_member_session(
        user_id=user_id,
        session_id=session_id,
    )

    status = session.get("status")

    if status == SessionStatus.COMPLETED.value:
        return session, await get_transaction_for_session(session_id)

    if status != SessionStatus.ACTIVE.value:
        raise BusinessRuleError(f"Cannot stop member session in status {status}")

    result = await stop_member_session(
        session_id=session_id,
        stop_reason=stop_reason,
    )

    return result["session"], result["transaction"]


async def process_member_session(session: dict) -> dict:
    """Refresh usage of one ACTIVE member session and stop it when the budget runs out."""
    session, budget_exhausted = await refresh_session_usage(session)

    if not budget_exhausted:
        return {
            "session": session,
            "auto_stopped": False,
            "stop_reason": None,
        }

    stop_reason = _budget_stop_reason(session)
    result = await stop_member_session(
        session_id=session["session_id"],
        stop_reason=stop_reason,
    )

    return {
        "session": result["session"],
        "transaction": result["transaction"],
        "auto_stopped": True,
        "stop_reason": stop_reason,
    }


async def process_member_telemetry(
    *,
    station_id: str,
    telemetry: dict,
) -> dict | None:
    session = await sessions.find_one(
        {
            "station_id": station_id,
            "type": SessionType.MEMBER.value,
            "status": SessionStatus.ACTIVE.value,
        },
        {"_id": 0},
    )
    if not session:
        return None

    return await process_member_session(session)


async def list_active_member_sessions() -> list[dict]:
    cursor = sessions.find(
        {
            "type": SessionType.MEMBER.value,
            "status": SessionStatus.ACTIVE.value,
        },
        {"_id": 0},
    )
    return await cursor.to_list(length=None)


async def get_current_member_session(*, user_id: str) -> dict:
    session = await sessions.find_one(
        {
            "user_id": user_id,
            "type": SessionType.MEMBER.value,
            "status": SessionStatus.ACTIVE.value,
        },
        {"_id": 0},
    )
    if not session:
        raise ResourceNotFoundError("No active charging session")

    session, _ = await refresh_session_usage(session)
    return session


def session_live_fields(session: dict) -> dict:
    """Derived values the member app shows while charging and in the result screen."""
    started_at = _as_utc(session["started_at"])
    ended_at = _as_utc(session["ended_at"]) if session.get("ended_at") else now_utc()
    budget = session.get("max_budget_vnd")
    cost_vnd = int(session.get("cost_vnd", 0))

    return {
        "duration_seconds": max(int((ended_at - started_at).total_seconds()), 0),
        "remaining_budget_vnd": max(int(budget) - cost_vnd, 0) if budget is not None else None,
    }


async def get_member_wallet(*, user_id: str) -> dict:
    user = await users.find_one(
        {"user_id": user_id},
        {
            "_id": 0,
            "user_id": 1,
            "balance_vnd": 1,
            "held_balance_vnd": 1,
        },
    )

    if not user:
        raise ResourceNotFoundError("Member user not found")

    balance_vnd = int(user.get("balance_vnd", 0))
    held_balance_vnd = int(user.get("held_balance_vnd", 0) or 0)

    return {
        "user_id": user["user_id"],
        "balance_vnd": balance_vnd,
        "held_balance_vnd": held_balance_vnd,
        "available_balance_vnd": max(balance_vnd - held_balance_vnd, 0),
    }


_TRANSACTION_PROJECTION = {
    "_id": 0,
    "transaction_id": 1,
    "type": 1,
    "session_id": 1,
    "amount_vnd": 1,
    "balance_before_vnd": 1,
    "balance_after_vnd": 1,
    "status": 1,
    "created_at": 1,
}


async def list_member_transactions(
    *,
    user_id: str,
    limit: int,
    offset: int,
) -> list[dict]:
    cursor = (
        transactions.find({"user_id": user_id}, _TRANSACTION_PROJECTION)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )

    return await cursor.to_list(length=limit)


async def get_member_transaction(
    *,
    user_id: str,
    transaction_id: str,
) -> dict:
    transaction = await transactions.find_one(
        {
            "transaction_id": transaction_id,
            "user_id": user_id,
        },
        _TRANSACTION_PROJECTION,
    )

    if not transaction:
        raise ResourceNotFoundError("Member transaction not found")

    return transaction


async def list_member_sessions(
    *,
    user_id: str,
    limit: int,
    offset: int,
) -> list[dict]:
    cursor = (
        sessions.find(
            {
                "user_id": user_id,
                "type": SessionType.MEMBER.value,
                "status": {"$ne": SessionStatus.ACTIVE.value},
            },
            {
                "_id": 0,
                "session_id": 1,
                "station_id": 1,
                "user_id": 1,
                "status": 1,
                "max_budget_vnd": 1,
                "energy_kwh": 1,
                "power_kw": 1,
                "price_per_kwh_vnd": 1,
                "cost_vnd": 1,
                "started_at": 1,
                "ended_at": 1,
                "stop_reason": 1,
            },
        )
        .sort("started_at", -1)
        .skip(offset)
        .limit(limit)
    )

    items = await cursor.to_list(length=limit)

    station_ids = list({session["station_id"] for session in items})
    names = {
        station["station_id"]: station.get("name")
        async for station in stations.find(
            {"station_id": {"$in": station_ids}},
            {"_id": 0, "station_id": 1, "name": 1},
        )
    }

    return [
        {
            **serialize_session(session),
            **session_live_fields(session),
            "station_name": names.get(session["station_id"]),
        }
        for session in items
    ]
