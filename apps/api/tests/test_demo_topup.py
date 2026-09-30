from uuid import uuid4

from app.db.mongodb import get_collection
from app.modules.payment import topup_service
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
transactions = get_collection("transactions")


def test_demo_topup_adds_balance_and_transaction(monkeypatch):
    async def scenario():
        user_id = f"topup-test-{uuid4()}"

        await users.insert_one(
            {
                "user_id": user_id,
                "name": "Demo Topup Test",
                "email": f"{user_id}@test.local",
                "password_hash": "test",
                "role": "USER",
                "balance_vnd": 100_000,
                "held_balance_vnd": 0,
                "is_active": True,
                "created_at": topup_service.datetime.now(topup_service.UTC),
                "updated_at": topup_service.datetime.now(topup_service.UTC),
            }
        )

        try:
            monkeypatch.setattr(
                topup_service.settings,
                "demo_topup_enabled",
                True,
            )

            result = await topup_service.demo_topup(
                user_id=user_id,
                amount_vnd=1_000_000,
            )

            assert result["type"] == "TOPUP"
            assert result["amount_vnd"] == 1_000_000
            assert result["balance_before_vnd"] == 100_000
            assert result["balance_after_vnd"] == 1_100_000
            assert result["status"] == "COMPLETED"

            user = await users.find_one(
                {"user_id": user_id},
                {"_id": 0, "balance_vnd": 1},
            )
            assert user["balance_vnd"] == 1_100_000

            stored = await transactions.find_one(
                {
                    "transaction_id": result["transaction_id"],
                },
                {"_id": 0},
            )
            assert stored["type"] == "TOPUP"
            assert stored["amount_vnd"] == 1_000_000
            assert stored["balance_before_vnd"] == 100_000
            assert stored["balance_after_vnd"] == 1_100_000

        finally:
            await transactions.delete_many({"user_id": user_id})
            await users.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())