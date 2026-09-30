"""Read-only admin views: sessions (A05, A09), transactions & payments (A06),
wallet holds (A07), dashboard (A10), maintenance (A11), errors (A12),
audit logs (A13) and electricity price (A04)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.dependencies import CurrentAdmin
from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.core.mongo_types import from_decimal128
from app.db.mongodb import get_collection
from app.modules.admin.common import Limit, Offset, http_errors, page, time_range
from app.modules.admin.schemas import (
    AdminAuditLog,
    AdminCurrentPrice,
    AdminDashboard,
    AdminErrorLog,
    AdminMaintenance,
    AdminPayment,
    AdminPrice,
    AdminPriceUpdate,
    AdminSession,
    AdminSessionDetail,
    AdminTransaction,
    AdminWalletHold,
    Page,
)
from app.modules.admin.views import (
    error_views,
    hold_views,
    maintenance_views,
    payment_views,
    session_views,
    transaction_views,
)
from app.modules.billing.service import cost_for_energy, energy_for_seconds
from app.modules.charging.constants import SessionStatus
from app.modules.charging.usage import refresh_session_usage
from app.modules.logs.service import record_audit
from app.modules.pricing.service import (
    get_current_price_record,
    list_price_history,
    set_price,
)
from app.modules.station.service import StationAvailability, list_stations

router = APIRouter(tags=["Admin · Monitoring"])
settings = get_settings()

sessions = get_collection("charging_sessions")
transactions = get_collection("transactions")
payment_intents = get_collection("payment_intents")
wallet_holds = get_collection("wallet_holds")
maintenance_records = get_collection("maintenance_records")
error_logs = get_collection("error_logs")
audit_logs = get_collection("audit_logs")
users = get_collection("users")

LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_OPEN = [SessionStatus.ACTIVE.value, SessionStatus.WAITING_PAYMENT.value]
# Money that actually reached the operator.
_REVENUE_TYPES = ["CHARGING", "GUEST_PAYMENT"]


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _refresh_active(items: list[dict]) -> list[dict]:
    result = []
    for session in items:
        if session["status"] == SessionStatus.ACTIVE.value:
            session, _ = await refresh_session_usage(session)
        result.append(session)
    return result


async def _find_page(collection, query: dict, sort_field: str, limit: int, offset: int):
    items = await (
        collection.find(query, {"_id": 0}).sort(sort_field, -1).skip(offset).limit(limit)
    ).to_list(length=limit)
    return items, await collection.count_documents(query)


# ---------------------------------------------------------------------------
# Sessions (A05, A09)
# ---------------------------------------------------------------------------


async def list_sessions(
    *,
    status: str | None,
    session_type: str | None,
    station_id: str | None,
    user_id: str | None,
    stop_reason: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    offset: int,
) -> dict:
    query = time_range("started_at", date_from, date_to)
    for field, value in (
        ("status", status),
        ("type", session_type),
        ("station_id", station_id),
        ("user_id", user_id),
        ("stop_reason", stop_reason),
    ):
        if value:
            query[field] = value

    items, total = await _find_page(sessions, query, "started_at", limit, offset)
    items = await _refresh_active(items)
    return page(await session_views(items), total, limit, offset)


async def live_sessions() -> list[dict]:
    items = await sessions.find({"status": {"$in": _OPEN}}, {"_id": 0}).sort(
        "started_at", -1
    ).to_list(length=None)
    return await session_views(await _refresh_active(items))


async def get_session_detail(session_id: str) -> dict:
    session = await sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise ResourceNotFoundError("Charging session not found")
    session = (await _refresh_active([session]))[0]

    tx = await transactions.find({"session_id": session_id}, {"_id": 0}).sort(
        "created_at", -1
    ).to_list(length=None)
    payments = await payment_intents.find({"session_id": session_id}, {"_id": 0}).sort(
        "created_at", -1
    ).to_list(length=None)

    return {
        **(await session_views([session]))[0],
        "transactions": await transaction_views(tx),
        "payments": await payment_views(payments),
    }


# ---------------------------------------------------------------------------
# Money (A06, A07)
# ---------------------------------------------------------------------------


async def list_transactions(
    *,
    tx_type: str | None,
    status: str | None,
    user_id: str | None,
    session_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    offset: int,
) -> dict:
    query = time_range("created_at", date_from, date_to)
    for field, value in (
        ("type", tx_type),
        ("status", status),
        ("user_id", user_id),
        ("session_id", session_id),
    ):
        if value:
            query[field] = value

    items, total = await _find_page(transactions, query, "created_at", limit, offset)
    return page(await transaction_views(items), total, limit, offset)


async def list_payments(
    *,
    status: str | None,
    station_id: str | None,
    session_id: str | None,
    limit: int,
    offset: int,
) -> dict:
    query = {}
    for field, value in (("status", status), ("station_id", station_id), ("session_id", session_id)):
        if value:
            query[field] = value

    items, total = await _find_page(payment_intents, query, "created_at", limit, offset)
    return page(await payment_views(items), total, limit, offset)


async def list_wallet_holds(*, status: str | None, user_id: str | None, limit: int, offset: int):
    query = {}
    if status:
        query["status"] = status
    if user_id:
        query["user_id"] = user_id

    items, total = await _find_page(wallet_holds, query, "created_at", limit, offset)
    return page(await hold_views(items), total, limit, offset)


# ---------------------------------------------------------------------------
# Logs (A11, A12, A13)
# ---------------------------------------------------------------------------


async def list_maintenance(*, status: str | None, station_id: str | None, limit: int, offset: int):
    query = {}
    if status:
        query["status"] = status
    if station_id:
        query["station_id"] = station_id

    items, total = await _find_page(maintenance_records, query, "started_at", limit, offset)
    return page(await maintenance_views(items), total, limit, offset)


async def list_errors(
    *,
    resolved: bool | None,
    station_id: str | None,
    severity: str | None,
    error_type: str | None,
    limit: int,
    offset: int,
) -> dict:
    query: dict = {}
    if resolved is not None:
        query["resolved_at"] = {"$ne": None} if resolved else None
    for field, value in (("station_id", station_id), ("severity", severity), ("error_type", error_type)):
        if value:
            query[field] = value

    items, total = await _find_page(error_logs, query, "occurred_at", limit, offset)
    return page(await error_views(items), total, limit, offset)


async def resolve_error(error_id: str, actor: dict) -> dict:
    error = await error_logs.find_one_and_update(
        {"error_id": error_id, "resolved_at": None},
        {"$set": {"resolved_at": now_utc(), "resolved_by": actor["user_id"]}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not error:
        existing = await error_logs.find_one({"error_id": error_id}, {"_id": 0})
        if not existing:
            raise ResourceNotFoundError("Error log not found")
        raise BusinessRuleError("Error is already resolved")

    await record_audit(
        actor=actor,
        action="ERROR_RESOLVE",
        target_type="error_log",
        target_id=error_id,
        metadata={"station_id": error["station_id"], "error_type": error["error_type"]},
    )
    return (await error_views([error]))[0]


async def list_audit_logs(
    *,
    actor_user_id: str | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    offset: int,
) -> dict:
    query = time_range("created_at", date_from, date_to)
    for field, value in (
        ("actor_user_id", actor_user_id),
        ("action", action),
        ("target_type", target_type),
        ("target_id", target_id),
    ):
        if value:
            query[field] = value

    items, total = await _find_page(audit_logs, query, "created_at", limit, offset)
    return page(items, total, limit, offset)


# ---------------------------------------------------------------------------
# Price (A04)
# ---------------------------------------------------------------------------


def _tick_cost(price: int) -> int:
    return cost_for_energy(
        energy_for_seconds(settings.energy_tick_seconds, settings.energy_power_kw_decimal),
        price,
    )


async def current_price() -> dict:
    record = await get_current_price_record()
    price = int(record["price_per_kwh_vnd"]) if record else settings.price_per_kwh_vnd
    return {
        "price_per_kwh_vnd": price,
        "tick_cost_vnd": _tick_cost(price),
        "effective_from": record["effective_from"] if record else None,
    }


async def update_price(data: AdminPriceUpdate, actor: dict) -> dict:
    record = await set_price(
        price_per_kwh_vnd=data.price_per_kwh_vnd,
        note=data.note,
        actor=actor,
    )
    await record_audit(
        actor=actor,
        action="PRICE_UPDATE",
        target_type="price",
        target_id="price_per_kwh_vnd",
        metadata={
            "from": record["previous_price_per_kwh_vnd"],
            "to": record["price_per_kwh_vnd"],
            "note": data.note,
        },
    )
    return record


# ---------------------------------------------------------------------------
# Dashboard (A10)
# ---------------------------------------------------------------------------


def _local_day_start(days_ago: int = 0) -> datetime:
    local_now = datetime.now(LOCAL_TZ)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return start.astimezone(UTC)


async def _daily_stats(since: datetime) -> dict[str, dict]:
    """Completed sessions and revenue per local day since `since`."""
    days: dict[str, dict] = {}

    session_pipeline = [
        {"$match": {"ended_at": {"$gte": since}, "status": SessionStatus.COMPLETED.value}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$ended_at",
                        "timezone": "Asia/Ho_Chi_Minh",
                    }
                },
                "sessions": {"$sum": 1},
                "energy_kwh": {"$sum": "$energy_kwh"},
            }
        },
    ]
    async for row in await sessions.aggregate(session_pipeline):
        days[row["_id"]] = {
            "sessions": row["sessions"],
            "energy_kwh": from_decimal128(row["energy_kwh"]),
            "revenue_vnd": 0,
        }

    revenue_pipeline = [
        {
            "$match": {
                "created_at": {"$gte": since},
                "type": {"$in": _REVENUE_TYPES},
                "status": "COMPLETED",
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at",
                        "timezone": "Asia/Ho_Chi_Minh",
                    }
                },
                "revenue_vnd": {"$sum": "$amount_vnd"},
            }
        },
    ]
    async for row in await transactions.aggregate(revenue_pipeline):
        day = days.setdefault(row["_id"], {"sessions": 0, "energy_kwh": Decimal(0), "revenue_vnd": 0})
        day["revenue_vnd"] = int(row["revenue_vnd"])

    return days


async def dashboard() -> dict:
    all_stations = await list_stations()
    by_availability = {a.value: 0 for a in StationAvailability}
    for station in all_stations:
        by_availability[station["availability"]] += 1

    today = _local_day_start()
    week_start = _local_day_start(6)
    daily = await _daily_stats(week_start)

    last_7_days = []
    for days_ago in range(6, -1, -1):
        key = _local_day_start(days_ago).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        stats = daily.get(key, {"sessions": 0, "energy_kwh": Decimal(0), "revenue_vnd": 0})
        last_7_days.append({"date": key, **stats})
    today_stats = last_7_days[-1]

    live = await live_sessions()
    recent_errors = await error_logs.find({}, {"_id": 0}).sort("occurred_at", -1).limit(5).to_list(
        length=5
    )
    price = await current_price()

    return {
        "generated_at": now_utc(),
        "stations_total": len(all_stations),
        "stations_by_availability": by_availability,
        "sessions_active": sum(1 for s in live if s["status"] == SessionStatus.ACTIVE.value),
        "sessions_waiting_payment": sum(
            1 for s in live if s["status"] == SessionStatus.WAITING_PAYMENT.value
        ),
        "sessions_completed_today": await sessions.count_documents(
            {"status": SessionStatus.COMPLETED.value, "ended_at": {"$gte": today}}
        ),
        "energy_today_kwh": today_stats["energy_kwh"],
        "revenue_today_vnd": today_stats["revenue_vnd"],
        "members_total": await users.count_documents({"role": "USER"}),
        "members_active": await users.count_documents({"role": "USER", "is_active": True}),
        "open_errors": await error_logs.count_documents({"resolved_at": None}),
        "maintenance_in_progress": await maintenance_records.count_documents(
            {"status": "IN_PROGRESS"}
        ),
        "price_per_kwh_vnd": price["price_per_kwh_vnd"],
        "last_7_days": last_7_days,
        "live_sessions": live,
        "recent_errors": await error_views(recent_errors),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

SessionStatusFilter = Literal["ACTIVE", "WAITING_PAYMENT", "COMPLETED", "ERROR"]


@router.get("/dashboard", response_model=AdminDashboard)
async def get_dashboard(admin: CurrentAdmin):
    return await dashboard()


@router.get("/sessions", response_model=Page[AdminSession])
async def get_sessions(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    status: SessionStatusFilter | None = None,
    type: Literal["MEMBER", "GUEST"] | None = None,
    station_id: str | None = None,
    user_id: str | None = None,
    stop_reason: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return await list_sessions(
        status=status,
        session_type=type,
        station_id=station_id,
        user_id=user_id,
        stop_reason=stop_reason,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/monitor/sessions", response_model=list[AdminSession])
async def get_live_sessions(admin: CurrentAdmin):
    """Open sessions with usage up to now; subscribe to /ws?token=... for updates."""
    return await live_sessions()


@router.get("/sessions/{session_id}", response_model=AdminSessionDetail)
async def get_session(session_id: str, admin: CurrentAdmin):
    with http_errors():
        return await get_session_detail(session_id)


@router.get("/transactions", response_model=Page[AdminTransaction])
async def get_transactions(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    type: Literal["CHARGING", "GUEST_PAYMENT", "TOPUP"] | None = None,
    status: Literal["COMPLETED", "FAILED"] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return await list_transactions(
        tx_type=type,
        status=status,
        user_id=user_id,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/payments", response_model=Page[AdminPayment])
async def get_payments(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["PENDING", "PAID", "EXPIRED", "CANCELLED"] | None = None,
    station_id: str | None = None,
    session_id: str | None = None,
):
    return await list_payments(
        status=status,
        station_id=station_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.get("/wallet-holds", response_model=Page[AdminWalletHold])
async def get_wallet_holds(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["HOLDING", "RELEASED", "CANCELLED"] | None = None,
    user_id: str | None = None,
):
    """Wallet holds are not used in v1 (DD 1), so this is normally empty."""
    return await list_wallet_holds(status=status, user_id=user_id, limit=limit, offset=offset)


@router.get("/maintenance", response_model=Page[AdminMaintenance])
async def get_maintenance(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    status: Literal["IN_PROGRESS", "COMPLETED", "CANCELLED"] | None = None,
    station_id: str | None = None,
):
    return await list_maintenance(status=status, station_id=station_id, limit=limit, offset=offset)


@router.get("/errors", response_model=Page[AdminErrorLog])
async def get_errors(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    resolved: bool | None = None,
    station_id: str | None = None,
    severity: Literal["WARNING", "ERROR", "CRITICAL"] | None = None,
    error_type: str | None = None,
):
    return await list_errors(
        resolved=resolved,
        station_id=station_id,
        severity=severity,
        error_type=error_type,
        limit=limit,
        offset=offset,
    )


@router.post("/errors/{error_id}/resolve", response_model=AdminErrorLog)
async def post_resolve_error(error_id: str, admin: CurrentAdmin):
    with http_errors():
        return await resolve_error(error_id, admin)


@router.get("/audit-logs", response_model=Page[AdminAuditLog])
async def get_audit_logs(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    actor_user_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    return await list_audit_logs(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/prices/current", response_model=AdminCurrentPrice)
async def get_current_price(admin: CurrentAdmin):
    return await current_price()


@router.get("/prices", response_model=Page[AdminPrice])
async def get_price_history(admin: CurrentAdmin, limit: Limit = 50, offset: Offset = 0):
    items, total = await list_price_history(limit=limit, offset=offset)
    return page(items, total, limit, offset)


@router.post("/prices", response_model=AdminPrice, status_code=201)
async def post_price(data: AdminPriceUpdate, admin: CurrentAdmin):
    """Set a new price; only sessions started afterwards use it (DD 5)."""
    with http_errors():
        return await update_price(data, admin)
