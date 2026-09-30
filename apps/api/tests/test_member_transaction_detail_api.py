from datetime import UTC, datetime
from uuid import uuid4

import httpx

from app.core.dependencies import get_current_user
from app.db.mongodb import get_collection
from app.main import app
from tests.shared_loop import TEST_LOOP

transactions = get_collection("transactions")


def test_member_transaction_detail_api():
    async def scenario():
        user_id = f"transaction-detail-test-{uuid4()}"
        other_user_id = f"other-user-{uuid4()}"

        own_transaction_id = str(uuid4())
        other_transaction_id = str(uuid4())
        created_at = datetime.now(UTC)

        await transactions.insert_many(
            [
                {
                    "transaction_id": own_transaction_id,
                    "user_id": user_id,
                    "type": "TOPUP",
                    "session_id": None,
                    "amount_vnd": 1_000_000,
                    "balance_before_vnd": 100_000,
                    "balance_after_vnd": 1_100_000,
                    "status": "COMPLETED",
                    "created_at": created_at,
                },
                {
                    "transaction_id": other_transaction_id,
                    "user_id": other_user_id,
                    "type": "TOPUP",
                    "session_id": None,
                    "amount_vnd": 500_000,
                    "balance_before_vnd": 200_000,
                    "balance_after_vnd": 700_000,
                    "status": "COMPLETED",
                    "created_at": created_at,
                },
            ]
        )

        async def fake_current_user():
            return {
                "user_id": user_id,
                "name": "Transaction Detail Test",
                "email": f"{user_id}@test.local",
                "role": "USER",
                "is_active": True,
            }

        app.dependency_overrides[get_current_user] = fake_current_user

        try:
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                own_response = await client.get(
                    f"/api/v1/member/transactions/{own_transaction_id}"
                )

                other_response = await client.get(
                    f"/api/v1/member/transactions/{other_transaction_id}"
                )

                missing_response = await client.get(
                    f"/api/v1/member/transactions/{uuid4()}"
                )

            assert own_response.status_code == 200

            own_body = own_response.json()

            assert own_body["transaction_id"] == own_transaction_id
            assert own_body["type"] == "TOPUP"
            assert own_body["session_id"] is None
            assert own_body["amount_vnd"] == 1_000_000
            assert own_body["balance_before_vnd"] == 100_000
            assert own_body["balance_after_vnd"] == 1_100_000
            assert own_body["status"] == "COMPLETED"

            assert other_response.status_code == 404
            assert other_response.json()["detail"] == "Member transaction not found"

            assert missing_response.status_code == 404
            assert missing_response.json()["detail"] == "Member transaction not found"

        finally:
            app.dependency_overrides.pop(get_current_user, None)

            await transactions.delete_many(
                {
                    "transaction_id": {
                        "$in": [
                            own_transaction_id,
                            other_transaction_id,
                        ]
                    }
                }
            )

    TEST_LOOP.run_until_complete(scenario())