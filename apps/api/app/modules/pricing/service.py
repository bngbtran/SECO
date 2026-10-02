"""Electricity price and its history (UC-A04).

Sessions snapshot the price in force when they start (DD 5), so a change only
affects sessions started afterwards.
"""

from datetime import UTC, datetime
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError
from app.db.mongodb import client, get_collection

settings = get_settings()

prices = get_collection("price_history")

MIN_PRICE_VND = 500
MAX_PRICE_VND = 20_000


def now_utc() -> datetime:
    return datetime.now(UTC)


async def get_current_price_record() -> dict | None:
    return await prices.find_one({"effective_to": None}, {"_id": 0})


async def get_current_price() -> int:
    record = await get_current_price_record()
    # Fall back to the configured tariff on a database without price history.
    return int(record["price_per_kwh_vnd"]) if record else settings.price_per_kwh_vnd


async def list_price_history(*, limit: int, offset: int) -> tuple[list[dict], int]:
    cursor = prices.find({}, {"_id": 0}).sort("effective_from", -1).skip(offset).limit(limit)
    return await cursor.to_list(length=limit), await prices.count_documents({})


async def set_price(
    *,
    price_per_kwh_vnd: int,
    note: str | None,
    actor: dict,
) -> dict:
    if not MIN_PRICE_VND <= price_per_kwh_vnd <= MAX_PRICE_VND:
        raise BusinessRuleError(
            f"price_per_kwh_vnd must be between {MIN_PRICE_VND} and {MAX_PRICE_VND}"
        )

    current = await get_current_price_record()
    if current and int(current["price_per_kwh_vnd"]) == price_per_kwh_vnd:
        raise BusinessRuleError("New price equals the current price")

    now = now_utc()
    record = {
        "price_id": str(uuid4()),
        "price_per_kwh_vnd": price_per_kwh_vnd,
        "effective_from": now,
        "effective_to": None,
        "created_by": actor["user_id"],
        "created_by_name": actor.get("name"),
        "note": note,
        "created_at": now,
    }

    async def callback(mongo_session):
        await prices.update_many(
            {"effective_to": None},
            {"$set": {"effective_to": now}},
            session=mongo_session,
        )
        await prices.insert_one(record, session=mongo_session)

    try:
        async with client.start_session() as mongo_session:
            await mongo_session.with_transaction(callback)
    except DuplicateKeyError as exc:
        raise ConflictError("Price was changed concurrently, try again") from exc

    record.pop("_id", None)
    return {
        **record,
        "previous_price_per_kwh_vnd": int(current["price_per_kwh_vnd"]) if current else None,
    }
