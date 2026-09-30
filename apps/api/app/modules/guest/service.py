from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import client, get_collection
from app.modules.billing.settlement import (
    TransactionStatus,
    TransactionType,
    get_transaction_for_session,
)
from app.modules.charging.constants import (
    SessionStatus,
    SessionType,
    StationStatus,
    StopReason,
)
from app.modules.charging.service import (
    complete_session,
    create_guest_session,
    mark_session_waiting_payment,
)
from app.modules.charging.usage import (
    _as_utc,
    refresh_session_usage,
    serialize_session,
    session_live_fields,
)
from app.modules.payment.constants import PaymentProvider, PaymentStatus

settings = get_settings()

sessions = get_collection("charging_sessions")
stations = get_collection("charging_stations")
payment_intents = get_collection("payment_intents")
transactions = get_collection("transactions")

# Station states a technician has to clear; paying must not hide them.
_STICKY_STATION_STATUSES = {StationStatus.ERROR.value, StationStatus.MAINTENANCE.value}


def now_utc() -> datetime:
    return datetime.now(UTC)


def validate_guest_budget(max_budget_vnd: int | None) -> int | None:
    if max_budget_vnd is None:
        return None

    if max_budget_vnd <= 0:
        raise BusinessRuleError("max_budget_vnd must be greater than 0")

    if max_budget_vnd > settings.guest_max_budget_vnd:
        raise BusinessRuleError(
            f"max_budget_vnd must not exceed {settings.guest_max_budget_vnd}"
        )

    return max_budget_vnd


async def start_guest_charging(
    *,
    station_id: str,
    max_budget_vnd: int | None,
) -> dict:
    """UC-G02/G03: guest pressed START (optionally with a budget) after plugging in."""
    return await create_guest_session(
        station_id=station_id,
        max_budget_vnd=validate_guest_budget(max_budget_vnd),
    )


async def get_guest_session(session_id: str) -> dict:
    session = await sessions.find_one(
        {"session_id": session_id, "type": SessionType.GUEST.value},
        {"_id": 0},
    )
    if not session:
        raise ResourceNotFoundError("Guest charging session not found")
    return session


async def list_active_guest_sessions() -> list[dict]:
    cursor = sessions.find(
        {"type": SessionType.GUEST.value, "status": SessionStatus.ACTIVE.value},
        {"_id": 0},
    )
    return await cursor.to_list(length=None)


# ---------------------------------------------------------------------------
# Payment intents (UC-G07, G10)
# ---------------------------------------------------------------------------


def _is_expired(payment: dict) -> bool:
    return _as_utc(payment["expires_at"]) <= now_utc()


async def _mark_expired(payment: dict) -> dict:
    updated = await payment_intents.find_one_and_update(
        {"payment_id": payment["payment_id"], "status": PaymentStatus.PENDING.value},
        {"$set": {"status": PaymentStatus.EXPIRED.value, "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return updated or await get_payment(payment["payment_id"])


async def get_payment(payment_id: str) -> dict:
    payment = await payment_intents.find_one({"payment_id": payment_id}, {"_id": 0})
    if not payment:
        raise ResourceNotFoundError("Payment not found")
    return payment


async def get_latest_payment(session_id: str) -> dict | None:
    return await payment_intents.find_one(
        {"session_id": session_id},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


async def create_payment_intent(session: dict) -> dict:
    """Return the session's open QR, creating a new one if none is valid."""
    if session["status"] != SessionStatus.WAITING_PAYMENT.value:
        raise BusinessRuleError("Session is not waiting for payment")

    session_id = session["session_id"]

    if await payment_intents.find_one(
        {"session_id": session_id, "status": PaymentStatus.PAID.value},
        {"_id": 1},
    ):
        raise ConflictError("Session is already paid")

    pending = await payment_intents.find_one(
        {"session_id": session_id, "status": PaymentStatus.PENDING.value},
        {"_id": 0},
    )
    if pending:
        if not _is_expired(pending):
            return pending
        await _mark_expired(pending)

    now = now_utc()
    payment_id = str(uuid4())
    payment = {
        "payment_id": payment_id,
        "session_id": session_id,
        "station_id": session["station_id"],
        "amount_vnd": int(session["cost_vnd"]),
        "status": PaymentStatus.PENDING.value,
        "provider": PaymentProvider.SANDBOX.value,
        "qr_payload": f"{settings.guest_payment_url_base.rstrip('/')}/{payment_id}",
        "created_at": now,
        "expires_at": now + timedelta(seconds=settings.guest_payment_ttl_seconds),
        "paid_at": None,
        "updated_at": now,
    }

    try:
        await payment_intents.insert_one(payment)
    except DuplicateKeyError:
        # Another request created the open QR first.
        existing = await payment_intents.find_one(
            {"session_id": session_id, "status": PaymentStatus.PENDING.value},
            {"_id": 0},
        )
        if existing:
            return existing
        raise

    payment.pop("_id", None)
    return payment


async def request_new_payment(session_id: str) -> dict:
    """UC-G10: show a fresh QR after the previous one expired or was cancelled."""
    session = await get_guest_session(session_id)
    return await create_payment_intent(session)


async def cancel_payment(payment_id: str) -> dict:
    payment = await get_payment(payment_id)

    if payment["status"] == PaymentStatus.PAID.value:
        raise ConflictError("Payment is already paid")

    if payment["status"] != PaymentStatus.PENDING.value:
        return payment

    updated = await payment_intents.find_one_and_update(
        {"payment_id": payment_id, "status": PaymentStatus.PENDING.value},
        {"$set": {"status": PaymentStatus.CANCELLED.value, "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return updated or await get_payment(payment_id)


async def expire_overdue_payments() -> list[dict]:
    overdue = await payment_intents.find(
        {"status": PaymentStatus.PENDING.value, "expires_at": {"$lte": now_utc()}},
        {"_id": 0},
    ).to_list(length=None)

    expired = []
    for payment in overdue:
        updated = await _mark_expired(payment)
        if updated["status"] == PaymentStatus.EXPIRED.value:
            expired.append(updated)
    return expired


# ---------------------------------------------------------------------------
# Stopping (UC-G05, G06)
# ---------------------------------------------------------------------------


async def stop_guest_session(
    *,
    session_id: str,
    stop_reason: str | StopReason,
    station_status: str | StationStatus | None = None,
) -> dict:
    """Stop a guest session.

    A session with a cost moves to WAITING_PAYMENT: the connector stays locked
    and the station stays bound to it until the QR is paid. A free session
    (cost 0) completes immediately.
    """
    active = await sessions.find_one(
        {
            "session_id": session_id,
            "type": SessionType.GUEST.value,
            "status": SessionStatus.ACTIVE.value,
        },
        {"_id": 0},
    )

    if not active:
        # Already stopped by another trigger (unplug vs budget vs ticker).
        session = await get_guest_session(session_id)
        return {
            "session": session,
            "payment": await get_latest_payment(session_id),
            "requires_payment": session["status"] == SessionStatus.WAITING_PAYMENT.value,
            "stopped_now": False,
        }

    active, _ = await refresh_session_usage(active)

    if int(active.get("cost_vnd", 0)) == 0:
        session = await complete_session(
            session_id=session_id,
            stop_reason=stop_reason,
            final_station_status=station_status or StationStatus.READY,
        )
        return {
            "session": session,
            "payment": None,
            "requires_payment": False,
            "stopped_now": True,
        }

    try:
        session = await mark_session_waiting_payment(
            session_id=session_id,
            stop_reason=stop_reason,
        )
    except ResourceNotFoundError:
        session = await get_guest_session(session_id)
        return {
            "session": session,
            "payment": await get_latest_payment(session_id),
            "requires_payment": session["status"] == SessionStatus.WAITING_PAYMENT.value,
            "stopped_now": False,
        }

    if station_status is not None:
        await stations.update_one(
            {"station_id": session["station_id"], "current_session_id": session_id},
            {"$set": {"status": StationStatus(station_status).value, "updated_at": now_utc()}},
        )

    payment = await create_payment_intent(session)

    return {
        "session": session,
        "payment": payment,
        "requires_payment": True,
        "stopped_now": True,
    }


async def process_guest_session(session: dict) -> dict:
    """Refresh usage of one ACTIVE guest session and stop it when the budget runs out."""
    session, budget_exhausted = await refresh_session_usage(session)

    if not budget_exhausted:
        return {"session": session, "auto_stopped": False}

    result = await stop_guest_session(
        session_id=session["session_id"],
        stop_reason=StopReason.MAX_BUDGET,
    )
    return {**result, "auto_stopped": True}


# ---------------------------------------------------------------------------
# Paying (UC-G08, G09)
# ---------------------------------------------------------------------------


async def _finalize_paid(payment: dict) -> dict:
    """Complete the session of a PAID payment and free the station (idempotent)."""
    session = await get_guest_session(payment["session_id"])

    if session["status"] == SessionStatus.WAITING_PAYMENT.value:
        station = await stations.find_one(
            {"station_id": session["station_id"]},
            {"_id": 0, "status": 1},
        )
        station_status = (
            station["status"]
            if station and station.get("status") in _STICKY_STATION_STATUSES
            else StationStatus.READY.value
        )
        session = await complete_session(
            session_id=session["session_id"],
            stop_reason=session.get("stop_reason") or StopReason.UNPLUG,
            final_station_status=station_status,
        )

    return {
        "payment": payment,
        "session": session,
        "transaction": await get_transaction_for_session(
            session["session_id"],
            TransactionType.GUEST_PAYMENT,
        ),
    }


async def pay_payment(payment_id: str) -> dict:
    """Sandbox provider confirms the payment: PAID -> transaction -> unlock."""
    payment = await get_payment(payment_id)

    if payment["status"] == PaymentStatus.PAID.value:
        return {**await _finalize_paid(payment), "paid_now": False}

    if payment["status"] != PaymentStatus.PENDING.value:
        raise ConflictError(f"Payment is {payment['status']}; request a new QR")

    if _is_expired(payment):
        await _mark_expired(payment)
        raise ConflictError("Payment is EXPIRED; request a new QR")

    async def callback(mongo_session):
        now = now_utc()
        paid = await payment_intents.find_one_and_update(
            {
                "payment_id": payment_id,
                "status": PaymentStatus.PENDING.value,
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "status": PaymentStatus.PAID.value,
                    "paid_at": now,
                    "updated_at": now,
                }
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
            session=mongo_session,
        )
        if not paid:
            raise ConflictError("Payment changed during confirmation")

        transaction = {
            "transaction_id": str(uuid4()),
            "user_id": None,
            "type": TransactionType.GUEST_PAYMENT.value,
            "session_id": paid["session_id"],
            "payment_id": payment_id,
            "payment_method": PaymentProvider.SANDBOX.value,
            "amount_vnd": int(paid["amount_vnd"]),
            "status": TransactionStatus.COMPLETED.value,
            "created_at": now,
        }
        await transactions.insert_one(transaction, session=mongo_session)
        return paid

    try:
        async with client.start_session() as mongo_session:
            paid = await mongo_session.with_transaction(callback)
    except (ConflictError, DuplicateKeyError):
        # Lost a race with a concurrent confirmation: report the winner's result.
        payment = await get_payment(payment_id)
        if payment["status"] != PaymentStatus.PAID.value:
            raise ConflictError(f"Payment is {payment['status']}; request a new QR") from None
        return {**await _finalize_paid(payment), "paid_now": False}

    return {**await _finalize_paid(paid), "paid_now": True}


# ---------------------------------------------------------------------------
# Views (UC-G04 live status, G08 payment page, G11 receipt)
# ---------------------------------------------------------------------------


async def _station_name(station_id: str) -> str | None:
    station = await stations.find_one({"station_id": station_id}, {"_id": 0, "name": 1})
    return station.get("name") if station else None


def _payment_view(payment: dict | None) -> dict | None:
    if not payment:
        return None
    return {
        "payment_id": payment["payment_id"],
        "amount_vnd": payment["amount_vnd"],
        "status": payment["status"],
        "provider": payment["provider"],
        "qr_payload": payment["qr_payload"],
        "created_at": payment["created_at"],
        "expires_at": payment["expires_at"],
        "paid_at": payment.get("paid_at"),
    }


async def get_guest_session_view(session_id: str) -> dict:
    session = await get_guest_session(session_id)

    if session["status"] == SessionStatus.ACTIVE.value:
        session, _ = await refresh_session_usage(session)

    payment = await get_latest_payment(session_id)
    if payment and payment["status"] == PaymentStatus.PENDING.value and _is_expired(payment):
        payment = await _mark_expired(payment)

    transaction = await get_transaction_for_session(session_id, TransactionType.GUEST_PAYMENT)

    return {
        **serialize_session(session),
        **session_live_fields(session),
        "station_name": await _station_name(session["station_id"]),
        "payment": _payment_view(payment),
        "transaction_id": transaction["transaction_id"] if transaction else None,
    }


async def get_payment_view(payment_id: str) -> dict:
    payment = await get_payment(payment_id)
    if payment["status"] == PaymentStatus.PENDING.value and _is_expired(payment):
        payment = await _mark_expired(payment)

    session = await get_guest_session(payment["session_id"])

    return {
        **_payment_view(payment),
        "session_id": session["session_id"],
        "station_id": session["station_id"],
        "station_name": await _station_name(session["station_id"]),
        "energy_kwh": serialize_session(session)["energy_kwh"],
        "stop_reason": session.get("stop_reason"),
        **session_live_fields(session),
    }
