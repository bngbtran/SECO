from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.charging.constants import SessionStatus, SessionType

users = get_collection("users")
sessions = get_collection("charging_sessions")
wallet_holds = get_collection("wallet_holds")


class WalletHoldStatus(StrEnum):
    HOLDING = "HOLDING"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


def now_utc() -> datetime:
    return datetime.now(UTC)


def validate_hold_amount(
    balance_vnd: int,
    amount_vnd: int,
) -> int:
    if balance_vnd < 0:
        raise ValueError("balance_vnd must be >= 0")

    if amount_vnd <= 0:
        raise BusinessRuleError("hold amount must be > 0")

    if amount_vnd > balance_vnd:
        raise BusinessRuleError("hold amount cannot exceed balance")

    return amount_vnd


def available_balance_vnd(
    balance_vnd: int,
    held_balance_vnd: int,
) -> int:
    if balance_vnd < 0:
        raise ValueError("balance_vnd must be >= 0")

    if held_balance_vnd < 0:
        raise ValueError("held_balance_vnd must be >= 0")

    if held_balance_vnd > balance_vnd:
        raise BusinessRuleError("held balance cannot exceed balance")

    return balance_vnd - held_balance_vnd


async def get_hold_for_session(
    session_id: str,
) -> dict | None:
    return await wallet_holds.find_one(
        {"session_id": session_id},
        {"_id": 0},
    )


async def _reserve_user_balance(
    *,
    user_id: str,
    amount_vnd: int,
) -> bool:
    result = await users.update_one(
        {
            "user_id": user_id,
            "is_active": True,
            "role": "USER",
            "$expr": {
                "$gte": [
                    "$balance_vnd",
                    {
                        "$add": [
                            {"$ifNull": ["$held_balance_vnd", 0]},
                            amount_vnd,
                        ]
                    },
                ]
            },
        },
        {
            "$inc": {
                "held_balance_vnd": amount_vnd,
            }
        },
    )
    return result.modified_count == 1


async def _release_user_balance(
    *,
    user_id: str,
    amount_vnd: int,
) -> bool:
    result = await users.update_one(
        {
            "user_id": user_id,
            "$expr": {
                "$gte": [
                    {"$ifNull": ["$held_balance_vnd", 0]},
                    amount_vnd,
                ]
            },
        },
        {
            "$inc": {
                "held_balance_vnd": -amount_vnd,
            }
        },
    )
    return result.modified_count == 1


async def create_member_hold(
    *,
    user_id: str,
    session_id: str,
    amount_vnd: int,
) -> dict:
    session = await sessions.find_one(
        {"session_id": session_id},
        {"_id": 0},
    )
    if not session:
        raise ResourceNotFoundError("Charging session not found")

    if session.get("status") != SessionStatus.ACTIVE.value:
        raise BusinessRuleError("Only ACTIVE session can create wallet hold")

    if session.get("type") != SessionType.MEMBER.value:
        raise BusinessRuleError("Only MEMBER session can create wallet hold")

    if session.get("user_id") != user_id:
        raise BusinessRuleError("Session does not belong to user")

    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0},
    )
    if not user:
        raise ResourceNotFoundError("User not found")

    if not user.get("is_active"):
        raise BusinessRuleError("User is inactive")

    if user.get("role") != "USER":
        raise BusinessRuleError("Only USER can have wallet hold")

    balance_vnd = int(user.get("balance_vnd", 0))
    held_balance_vnd = int(user.get("held_balance_vnd", 0) or 0)

    if held_balance_vnd < 0 or held_balance_vnd > balance_vnd:
        raise BusinessRuleError("Invalid wallet held balance")

    available_balance_vnd(balance_vnd, held_balance_vnd)
    validate_hold_amount(balance_vnd, amount_vnd)

    existing = await get_hold_for_session(session_id)
    if existing:
        if (
            existing.get("user_id") == user_id
            and int(existing.get("amount_vnd", 0)) == amount_vnd
            and existing.get("status") == WalletHoldStatus.HOLDING.value
        ):
            return existing

        if existing.get("status") != WalletHoldStatus.HOLDING.value:
            raise ConflictError("Session already has a completed wallet hold")

        raise ConflictError("Session already has a different wallet hold")

    reserved = await _reserve_user_balance(
        user_id=user_id,
        amount_vnd=amount_vnd,
    )
    if not reserved:
        raise BusinessRuleError("Insufficient available balance")

    now = now_utc()
    document = {
        "wallet_hold_id": str(uuid4()),
        "user_id": user_id,
        "session_id": session_id,
        "amount_vnd": amount_vnd,
        "status": WalletHoldStatus.HOLDING.value,
        "created_at": now,
        "released_at": None,
    }

    try:
        await wallet_holds.insert_one(document)
    except DuplicateKeyError as exc:
        await _release_user_balance(
            user_id=user_id,
            amount_vnd=amount_vnd,
        )

        existing = await get_hold_for_session(session_id)
        if existing:
            if (
                existing.get("user_id") == user_id
                and int(existing.get("amount_vnd", 0)) == amount_vnd
                and existing.get("status") == WalletHoldStatus.HOLDING.value
            ):
                return existing

            raise ConflictError("Session already has a different wallet hold") from exc

        raise ConflictError("Could not create wallet hold") from exc
    except Exception:
        await _release_user_balance(
            user_id=user_id,
            amount_vnd=amount_vnd,
        )
        raise

    return document


async def release_member_hold(
    *,
    session_id: str,
    cancelled: bool = False,
) -> dict:
    existing = await get_hold_for_session(session_id)

    if not existing:
        raise ResourceNotFoundError("Wallet hold not found")

    if existing.get("status") != WalletHoldStatus.HOLDING.value:
        return existing

    user_id = existing["user_id"]
    amount_vnd = int(existing["amount_vnd"])
    new_status = (
        WalletHoldStatus.CANCELLED.value
        if cancelled
        else WalletHoldStatus.RELEASED.value
    )
    now = now_utc()

    result = await wallet_holds.find_one_and_update(
        {
            "session_id": session_id,
            "status": WalletHoldStatus.HOLDING.value,
        },
        {
            "$set": {
                "status": new_status,
                "released_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if result is None:
        return await get_hold_for_session(session_id)

    released = await _release_user_balance(
        user_id=user_id,
        amount_vnd=amount_vnd,
    )

    if not released:
        raise BusinessRuleError("Wallet held balance could not be released")

    return result
