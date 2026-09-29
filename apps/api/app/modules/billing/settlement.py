from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import client, get_collection
from app.modules.billing.wallet import WalletHoldStatus
from app.modules.charging.constants import SessionStatus, SessionType

sessions = get_collection("charging_sessions")
users = get_collection("users")
wallet_holds = get_collection("wallet_holds")
transactions = get_collection("transactions")


class TransactionType(StrEnum):
    CHARGING = "CHARGING"
    GUEST_PAYMENT = "GUEST_PAYMENT"


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
    transaction_type: TransactionType,
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
    existing = await get_transaction_for_session(
        session_id,
        TransactionType.CHARGING,
    )
    if existing:
        return existing

    async def callback(session):
        existing_in_tx = await transactions.find_one(
            {
                "session_id": session_id,
                "type": TransactionType.CHARGING.value,
            },
            {"_id": 0},
            session=session,
        )
        if existing_in_tx:
            return existing_in_tx

        charging_session = await sessions.find_one(
            {"session_id": session_id},
            {"_id": 0},
            session=session,
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

        cost_vnd = validate_settlement_cost(
            int(charging_session.get("cost_vnd", 0))
        )

        hold = await wallet_holds.find_one(
            {
                "session_id": session_id,
                "status": WalletHoldStatus.HOLDING.value,
            },
            {"_id": 0},
            session=session,
        )
        if not hold:
            raise BusinessRuleError("Active wallet hold not found")

        if hold.get("user_id") != user_id:
            raise BusinessRuleError("Wallet hold does not belong to session user")

        hold_amount_vnd = int(hold.get("amount_vnd", 0))
        if hold_amount_vnd <= 0:
            raise BusinessRuleError("Invalid wallet hold amount")

        if cost_vnd > hold_amount_vnd:
            raise BusinessRuleError("Settlement cost exceeds wallet hold")

        user = await users.find_one(
            {"user_id": user_id},
            {"_id": 0},
            session=session,
        )
        if not user:
            raise ResourceNotFoundError("User not found")

        balance_before_vnd = int(user.get("balance_vnd", 0))
        held_before_vnd = int(user.get("held_balance_vnd", 0) or 0)

        if balance_before_vnd < cost_vnd:
            raise BusinessRuleError("Insufficient balance for settlement")

        if held_before_vnd < hold_amount_vnd:
            raise BusinessRuleError("Wallet held balance is inconsistent")

        balance_after_vnd = balance_before_vnd - cost_vnd

        user_result = await users.update_one(
            {
                "user_id": user_id,
                "$expr": {
                    "$and": [
                        {
                            "$gte": [
                                "$balance_vnd",
                                cost_vnd,
                            ]
                        },
                        {
                            "$gte": [
                                {"$ifNull": ["$held_balance_vnd", 0]},
                                hold_amount_vnd,
                            ]
                        },
                    ]
                },
            },
            {
                "$inc": {
                    "balance_vnd": -cost_vnd,
                    "held_balance_vnd": -hold_amount_vnd,
                },
                "$set": {
                    "updated_at": now_utc(),
                },
            },
            session=session,
        )

        if user_result.modified_count != 1:
            raise BusinessRuleError("Wallet balance changed before settlement")

        transaction = {
            "transaction_id": str(uuid4()),
            "session_id": session_id,
            "user_id": user_id,
            "type": TransactionType.CHARGING.value,
            "amount_vnd": cost_vnd,
            "balance_before_vnd": balance_before_vnd,
            "balance_after_vnd": balance_after_vnd,
            "status": TransactionStatus.COMPLETED.value,
            "created_at": now_utc(),
        }

        await transactions.insert_one(
            transaction,
            session=session,
        )

        hold_result = await wallet_holds.find_one_and_update(
            {
                "session_id": session_id,
                "status": WalletHoldStatus.HOLDING.value,
            },
            {
                "$set": {
                    "status": WalletHoldStatus.RELEASED.value,
                    "released_at": now_utc(),
                }
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
            session=session,
        )

        if hold_result is None:
            raise ConflictError("Wallet hold changed during settlement")

        return transaction

    try:
        async with client.start_session().bind() as session:
            return await session.with_transaction(callback)
    except DuplicateKeyError:
        existing = await get_transaction_for_session(
            session_id,
            TransactionType.CHARGING,
        )
        if existing:
            return existing
        raise


async def record_guest_payment(
    *,
    session_id: str,
) -> dict:
    existing = await get_transaction_for_session(
        session_id,
        TransactionType.GUEST_PAYMENT,
    )
    if existing:
        return existing

    async def callback(session):
        existing_in_tx = await transactions.find_one(
            {
                "session_id": session_id,
                "type": TransactionType.GUEST_PAYMENT.value,
            },
            {"_id": 0},
            session=session,
        )
        if existing_in_tx:
            return existing_in_tx

        charging_session = await sessions.find_one(
            {"session_id": session_id},
            {"_id": 0},
            session=session,
        )
        if not charging_session:
            raise ResourceNotFoundError("Charging session not found")

        if charging_session.get("status") != SessionStatus.COMPLETED.value:
            raise BusinessRuleError("Session must be COMPLETED before payment settlement")

        if charging_session.get("type") != SessionType.GUEST.value:
            raise BusinessRuleError("Only GUEST session can use guest payment")

        cost_vnd = validate_settlement_cost(
            int(charging_session.get("cost_vnd", 0))
        )

        transaction = {
            "transaction_id": str(uuid4()),
            "session_id": session_id,
            "user_id": None,
            "type": TransactionType.GUEST_PAYMENT.value,
            "amount_vnd": cost_vnd,
            "balance_before_vnd": None,
            "balance_after_vnd": None,
            "status": TransactionStatus.COMPLETED.value,
            "created_at": now_utc(),
        }

        await transactions.insert_one(
            transaction,
            session=session,
        )

        return transaction

    try:
        async with client.start_session().bind() as session:
            return await session.with_transaction(callback)
    except DuplicateKeyError:
        existing = await get_transaction_for_session(
            session_id,
            TransactionType.GUEST_PAYMENT,
        )
        if existing:
            return existing
        raise
