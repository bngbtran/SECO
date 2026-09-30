from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.mongodb import get_collection
from app.modules.member.service import (
    get_member_wallet,
    list_member_transactions,
)
from tests.shared_loop import TEST_LOOP


def test_member_wallet_returns_balance_and_available_balance():
    async def scenario():
        users = get_collection("users")
        user_id = f"USER-{uuid4().hex[:8]}"

        await users.insert_one(
            {
                "user_id": user_id,
                "name": "Test Member",
                "email": f"{user_id}@example.com",
                "password_hash": "test",
                "role": "USER",
                "balance_vnd": 200_000,
                "held_balance_vnd": 50_000,
                "is_active": True,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

        try:
            result = await get_member_wallet(user_id=user_id)

            assert result["user_id"] == user_id
            assert result["balance_vnd"] == 200_000
            assert result["held_balance_vnd"] == 50_000
            assert result["available_balance_vnd"] == 150_000
        finally:
            await users.delete_one({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())


def test_member_transaction_history_is_newest_first():
    async def scenario():
        transactions = get_collection("transactions")
        user_id = f"USER-{uuid4().hex[:8]}"
        now = datetime.now(UTC)

        documents = [
            {
                "transaction_id": f"TX-{uuid4().hex[:8]}",
                "user_id": user_id,
                "type": "CHARGING",
                "session_id": "SESSION-001",
                "amount_vnd": 5,
                "balance_before_vnd": 200_000,
                "balance_after_vnd": 199_995,
                "status": "COMPLETED",
                "created_at": now,
            },
            {
                "transaction_id": f"TX-{uuid4().hex[:8]}",
                "user_id": user_id,
                "type": "CHARGING",
                "session_id": "SESSION-002",
                "amount_vnd": 10,
                "balance_before_vnd": 199_995,
                "balance_after_vnd": 199_985,
                "status": "COMPLETED",
                "created_at": now + timedelta(milliseconds=1),
            },
        ]

        await transactions.insert_many(documents)

        try:
            result = await list_member_transactions(
                user_id=user_id,
                limit=50,
                offset=0,
            )

            assert len(result) == 2
            assert result[0]["amount_vnd"] == 10
            assert result[1]["amount_vnd"] == 5
        finally:
            await transactions.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())
