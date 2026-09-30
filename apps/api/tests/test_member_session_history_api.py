from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from bson import Decimal128

from app.core.dependencies import get_current_user
from app.db.mongodb import get_collection
from app.main import app
from tests.shared_loop import TEST_LOOP

sessions = get_collection("charging_sessions")


def test_member_session_history_api():
    async def scenario():
        user_id = f"session-history-test-{uuid4()}"

        started_old = datetime.now(UTC) - timedelta(days=2)
        started_new = datetime.now(UTC) - timedelta(days=1)

        old_session_id = str(uuid4())
        new_session_id = str(uuid4())

        await sessions.insert_many(
            [
                {
                    "session_id": old_session_id,
                    "station_id": "EVCS-OLD",
                    "type": "MEMBER",
                    "user_id": user_id,
                    "status": "COMPLETED",
                    "max_budget_vnd": 50_000,
                    "energy_kwh": Decimal128("0.5"),
                    "power_kw": Decimal128("0.15"),
                    "price_per_kwh_vnd": 4000,
                    "cost_vnd": 2000,
                    "started_at": started_old,
                    "ended_at": started_old + timedelta(minutes=30),
                    "stop_reason": "UNPLUG",
                    "connector_locked": False,
                    "created_at": started_old,
                    "updated_at": started_old,
                },
                {
                    "session_id": new_session_id,
                    "station_id": "EVCS-NEW",
                    "type": "MEMBER",
                    "user_id": user_id,
                    "status": "COMPLETED",
                    "max_budget_vnd": 100_000,
                    "energy_kwh": Decimal128("1.0"),
                    "power_kw": Decimal128("0.15"),
                    "price_per_kwh_vnd": 4000,
                    "cost_vnd": 4000,
                    "started_at": started_new,
                    "ended_at": started_new + timedelta(minutes=30),
                    "stop_reason": "MAX_BUDGET",
                    "connector_locked": False,
                    "created_at": started_new,
                    "updated_at": started_new,
                },
            ]
        )

        async def fake_current_user():
            return {
                "user_id": user_id,
                "name": "Session History Test",
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
                response = await client.get(
                    "/api/v1/member/sessions",
                    params={
                        "limit": 50,
                        "offset": 0,
                    },
                )

            assert response.status_code == 200

            body = response.json()

            assert len(body) == 2

            assert body[0]["session_id"] == new_session_id
            assert body[0]["station_id"] == "EVCS-NEW"
            assert body[0]["status"] == "COMPLETED"
            assert body[0]["cost_vnd"] == 4000
            assert body[0]["stop_reason"] == "MAX_BUDGET"

            assert body[1]["session_id"] == old_session_id
            assert body[1]["station_id"] == "EVCS-OLD"
            assert body[1]["cost_vnd"] == 2000
            assert body[1]["stop_reason"] == "UNPLUG"

        finally:
            app.dependency_overrides.pop(get_current_user, None)
            await sessions.delete_many(
                {
                    "session_id": {
                        "$in": [
                            old_session_id,
                            new_session_id,
                        ]
                    }
                }
            )

    TEST_LOOP.run_until_complete(scenario())