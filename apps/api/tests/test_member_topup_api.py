from datetime import UTC, datetime
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.db.mongodb import get_collection
from app.main import app
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
transactions = get_collection("transactions")

settings = get_settings()


def test_member_topup_api(monkeypatch):
    async def scenario():
        user_id = f"topup-api-test-{uuid4()}"

        await users.insert_one(
            {
                "user_id": user_id,
                "name": "Topup API Test",
                "email": f"{user_id}@test.local",
                "password_hash": "test",
                "role": "USER",
                "balance_vnd": 100_000,
                "held_balance_vnd": 0,
                "is_active": True,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

        async def fake_current_user():
            return {
                "user_id": user_id,
                "name": "Topup API Test",
                "email": f"{user_id}@test.local",
                "role": "USER",
                "is_active": True,
            }

        app.dependency_overrides[get_current_user] = fake_current_user
        monkeypatch.setattr(settings, "demo_topup_enabled", True)

        try:
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/member/topup",
                    json={"amount_vnd": 1_000_000},
                )

            assert response.status_code == 200

            body = response.json()

            assert body["user_id"] == user_id
            assert body["amount_vnd"] == 1_000_000
            assert body["balance_before_vnd"] == 100_000
            assert body["balance_after_vnd"] == 1_100_000
            assert body["status"] == "COMPLETED"

            user = await users.find_one(
                {"user_id": user_id},
                {"_id": 0, "balance_vnd": 1},
            )
            assert user["balance_vnd"] == 1_100_000

            transaction = await transactions.find_one(
                {"transaction_id": body["transaction_id"]},
                {"_id": 0},
            )

            assert transaction["type"] == "TOPUP"
            assert transaction["user_id"] == user_id
            assert transaction["amount_vnd"] == 1_000_000
            assert transaction["balance_before_vnd"] == 100_000
            assert transaction["balance_after_vnd"] == 1_100_000
            assert transaction["status"] == "COMPLETED"

        finally:
            app.dependency_overrides.pop(get_current_user, None)
            await transactions.delete_many({"user_id": user_id})
            await users.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())