from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import client, get_collection
from app.modules.charging.constants import SessionStatus, SessionType

sessions = get_collection("charging_sessions")
users = get_collection("users")
transactions = get_collection("transactions")


class TransactionType(StrEnum):
    CHARGING = "CHARGING"
    GUEST_PAYMENT = "GUEST_PAYMENT"
    TOPUP = "TOPUP"


class TransactionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def now_utc() -> datetime:
    return datetime.now(UTC)


def validate_settlement_cost(cost_vnd: int) -> int:
    if cost_vnd < 0:
        raise BusinessRuleError("cost_vnd must be >= 0")
    return cost_vnd


async def get_transaction_for_session(
    session_id: str,
    transaction_type: TransactionType = TransactionType.CHARGING,
) -> dict | None:
    return await transactions.find_one(
        {
            "session_id": session_id,
            "type": transaction_type.value,
        },
        {"_id": 0},
    )


async def settle_member_session(
    *,
    session_id: str,
) -> dict:
    """Deduct the final session cost from the member wallet (DD v1: no wallet hold).

    Idempotent: a session is charged at most once, guarded by the unique
    (session_id, type) index on transactions.
    """
    existing = await get_transaction_for_session(session_id)
    if existing:
        return existing

    async def callback(mongo_session):
        existing_in_tx = await transactions.find_one(
            {
                "session_id": session_id,
                "type": TransactionType.CHARGING.value,
            },
            {"_id": 0},
            session=mongo_session,
        )
        if existing_in_tx:
            return existing_in_tx

        charging_session = await sessions.find_one(
            {"session_id": session_id},
            {"_id": 0},
            session=mongo_session,
        )
        if not charging_session:
            raise ResourceNotFoundError("Charging session not found")

        if charging_session.get("status") != SessionStatus.COMPLETED.value:
            raise BusinessRuleError("Session must be COMPLETED before settlement")

        if charging_session.get("type") != SessionType.MEMBER.value:
            raise BusinessRuleError("Only MEMBER session can use wallet settlement")

        user_id = charging_session.get("user_id")
        if not user_id:
            raise BusinessRuleError("MEMBER session is missing user_id")

        cost_vnd = validate_settlement_cost(int(charging_session.get("cost_vnd", 0)))

        user = await users.find_one(
            {"user_id": user_id},
            {"_id": 0, "balance_vnd": 1},
            session=mongo_session,
        )
        if not user:
            raise ResourceNotFoundError("User not found")

        balance_before_vnd = int(user.get("balance_vnd", 0))
        if balance_before_vnd < cost_vnd:
            # Budget is capped at the balance when the session starts, so this
            # only happens if the wallet changed underneath us.
            raise ConflictError("Insufficient balance for settlement")

        balance_after_vnd = balance_before_vnd - cost_vnd
        now = now_utc()

        user_result = await users.update_one(
            {
                "user_id": user_id,
                "balance_vnd": balance_before_vnd,
            },
            {
                "$set": {
                    "balance_vnd": balance_after_vnd,
                    "updated_at": now,
                }
            },
            session=mongo_session,
        )
        if user_result.modified_count != 1:
            raise ConflictError("Balance changed during settlement")

        transaction = {
            "transaction_id": str(uuid4()),
            "user_id": user_id,
            "type": TransactionType.CHARGING.value,
            "session_id": session_id,
            "amount_vnd": cost_vnd,
            "balance_before_vnd": balance_before_vnd,
            "balance_after_vnd": balance_after_vnd,
            "status": TransactionStatus.COMPLETED.value,
            "created_at": now,
        }

        await transactions.insert_one(transaction, session=mongo_session)
        transaction.pop("_id", None)
        return transaction

    try:
        async with client.start_session() as mongo_session:
            return await mongo_session.with_transaction(callback)
    except DuplicateKeyError:
        existing = await get_transaction_for_session(session_id)
        if existing:
            return existing
        raise


async def record_guest_payment(
    *,
    session_id: str,
    amount_vnd: int,
    payment_method: str = "SANDBOX",
) -> dict:
    if amount_vnd < 0:
        raise BusinessRuleError("Payment amount cannot be negative")

    session = await sessions.find_one(
        {
            "session_id": session_id,
            "type": SessionType.GUEST.value,
        },
        {"_id": 0},
    )
    if not session:
        raise ResourceNotFoundError("Guest charging session not found")

    existing = await get_transaction_for_session(
        session_id,
        TransactionType.GUEST_PAYMENT,
    )
    if existing:
        return existing

    transaction = {
        "transaction_id": str(uuid4()),
        "user_id": None,
        "type": TransactionType.GUEST_PAYMENT.value,
        "session_id": session_id,
        "amount_vnd": amount_vnd,
        "payment_method": payment_method,
        "status": TransactionStatus.COMPLETED.value,
        "created_at": now_utc(),
    }

    try:
        await transactions.insert_one(transaction)
    except DuplicateKeyError:
        return await get_transaction_for_session(
            session_id,
            TransactionType.GUEST_PAYMENT,
        )

    transaction.pop("_id", None)
    return transaction
