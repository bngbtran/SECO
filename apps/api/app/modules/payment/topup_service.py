from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, FeatureDisabledError, ResourceNotFoundError
from app.db.mongodb import client, get_collection

settings = get_settings()

users = get_collection("users")
transactions = get_collection("transactions")


async def demo_topup(
    *,
    user_id: str,
    amount_vnd: int,
) -> dict:
    if not settings.demo_topup_enabled:
        raise FeatureDisabledError("Demo top-up is disabled")

    if amount_vnd <= 0:
        raise BusinessRuleError("amount_vnd must be greater than 0")

    if amount_vnd > settings.demo_topup_max_vnd:
        raise BusinessRuleError(
            f"amount_vnd must not exceed {settings.demo_topup_max_vnd}"
        )

    async def callback(mongo_session):
        now = datetime.now(UTC)

        user = await users.find_one_and_update(
            {
                "user_id": user_id,
                "is_active": True,
                "role": "USER",
            },
            {
                "$inc": {"balance_vnd": amount_vnd},
                "$set": {"updated_at": now},
            },
            projection={"_id": 0, "balance_vnd": 1},
            return_document=ReturnDocument.AFTER,
            session=mongo_session,
        )

        if not user:
            raise ResourceNotFoundError("Member user not found")

        balance_after_vnd = int(user["balance_vnd"])

        transaction = {
            "transaction_id": str(uuid4()),
            "user_id": user_id,
            "type": "TOPUP",
            "session_id": None,
            "amount_vnd": amount_vnd,
            "balance_before_vnd": balance_after_vnd - amount_vnd,
            "balance_after_vnd": balance_after_vnd,
            "status": "COMPLETED",
            "created_at": now,
        }

        await transactions.insert_one(transaction, session=mongo_session)
        transaction.pop("_id", None)
        return transaction

    async with client.start_session() as mongo_session:
        return await mongo_session.with_transaction(callback)
