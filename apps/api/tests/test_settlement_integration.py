import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from bson.decimal128 import Decimal128

from app.db.mongodb import get_collection
from app.modules.billing.settlement import settle_member_session
from app.modules.billing.wallet import create_member_hold
from app.modules.charging.constants import SessionStatus, SessionType
from app.modules.charging.service import complete_session

users = get_collection("users")
sessions = get_collection("charging_sessions")
wallet_holds = get_collection("wallet_holds")
transactions = get_collection("transactions")
stations = get_collection("charging_stations")

_test_loop = asyncio.new_event_loop()



def test_member_settlement_is_atomic_and_idempotent():
    async def scenario():
        user_id = f"test-user-{uuid4()}"
        session_id = str(uuid4())
        station_id = f"TEST-{uuid4()}"
        now = datetime.now(UTC)

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Settlement Test",
                    "email": f"{user_id}@test.local",
                    "password_hash": "test",
                    "role": "USER",
                    "balance_vnd": 200_000,
                    "held_balance_vnd": 0,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "Settlement Test Station",
                    "status": "CHARGING",
                    "is_online": True,
                    "current_session_id": session_id,
                    "connector_lock": "UNLOCKED",
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await sessions.insert_one(
                {
                    "session_id": session_id,
                    "station_id": station_id,
                    "type": SessionType.MEMBER.value,
                    "user_id": user_id,
                    "status": SessionStatus.ACTIVE.value,
                    "max_budget_vnd": 50_000,
                    "energy_kwh": Decimal128("0"),
                    "power_kw": Decimal128("0.15"),
                    "price_per_kwh_vnd": 4000,
                    "cost_vnd": 5,
                    "stop_reason": None,
                    "started_at": now,
                    "ended_at": None,
                    "connector_locked": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            hold = await create_member_hold(
                user_id=user_id,
                session_id=session_id,
                amount_vnd=50_000,
            )

            user = await users.find_one(
                {"user_id": user_id},
                {"_id": 0, "balance_vnd": 1, "held_balance_vnd": 1},
            )
            assert user["balance_vnd"] == 200_000
            assert user["held_balance_vnd"] == 50_000
            assert hold["status"] == "HOLDING"

            await complete_session(
                session_id=session_id,
                stop_reason="UNPLUG",
            )

            transaction1 = await settle_member_session(
                session_id=session_id,
            )

            assert transaction1["type"] == "CHARGING"
            assert transaction1["amount_vnd"] == 5
            assert transaction1["balance_before_vnd"] == 200_000
            assert transaction1["balance_after_vnd"] == 199_995

            user = await users.find_one(
                {"user_id": user_id},
                {"_id": 0, "balance_vnd": 1, "held_balance_vnd": 1},
            )
            assert user["balance_vnd"] == 199_995
            assert user["held_balance_vnd"] == 0

            stored_hold = await wallet_holds.find_one(
                {"session_id": session_id},
                {"_id": 0},
            )
            assert stored_hold["status"] == "RELEASED"

            transaction2 = await settle_member_session(
                session_id=session_id,
            )

            assert transaction2["transaction_id"] == transaction1["transaction_id"]

            transaction_count = await transactions.count_documents(
                {
                    "session_id": session_id,
                    "type": "CHARGING",
                }
            )
            assert transaction_count == 1

            user = await users.find_one(
                {"user_id": user_id},
                {"_id": 0, "balance_vnd": 1, "held_balance_vnd": 1},
            )
            assert user["balance_vnd"] == 199_995
            assert user["held_balance_vnd"] == 0

        finally:
            await transactions.delete_many({"session_id": session_id})
            await wallet_holds.delete_many({"session_id": session_id})
            await sessions.delete_many({"session_id": session_id})
            await stations.delete_many({"station_id": station_id})
            await users.delete_many({"user_id": user_id})

    _test_loop.run_until_complete(scenario())


def test_member_settlement_concurrent_calls_charge_once():
    async def scenario():
        user_id = f"test-user-{uuid4()}"
        session_id = str(uuid4())
        station_id = f"TEST-{uuid4()}"
        now = datetime.now(UTC)

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Concurrent Settlement Test",
                    "email": f"{user_id}@test.local",
                    "password_hash": "test",
                    "role": "USER",
                    "balance_vnd": 200_000,
                    "held_balance_vnd": 0,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "Concurrent Settlement Test Station",
                    "status": "CHARGING",
                    "is_online": True,
                    "current_session_id": session_id,
                    "connector_lock": "UNLOCKED",
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await sessions.insert_one(
                {
                    "session_id": session_id,
                    "station_id": station_id,
                    "type": SessionType.MEMBER.value,
                    "user_id": user_id,
                    "status": SessionStatus.ACTIVE.value,
                    "max_budget_vnd": 50_000,
                    "energy_kwh": Decimal128("0"),
                    "power_kw": Decimal128("0.15"),
                    "price_per_kwh_vnd": 4000,
                    "cost_vnd": 5,
                    "stop_reason": None,
                    "started_at": now,
                    "ended_at": None,
                    "connector_locked": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await create_member_hold(
                user_id=user_id,
                session_id=session_id,
                amount_vnd=50_000,
            )

            await complete_session(
                session_id=session_id,
                stop_reason="UNPLUG",
            )

            results = await asyncio.gather(
                settle_member_session(session_id=session_id),
                settle_member_session(session_id=session_id),
            )

            assert results[0]["transaction_id"] == results[1]["transaction_id"]

            stored_transactions = await transactions.find(
                {
                    "session_id": session_id,
                    "type": "CHARGING",
                },
                {"_id": 0},
            ).to_list(length=10)

            assert len(stored_transactions) == 1
            assert stored_transactions[0]["amount_vnd"] == 5

            user = await users.find_one(
                {"user_id": user_id},
                {
                    "_id": 0,
                    "balance_vnd": 1,
                    "held_balance_vnd": 1,
                },
            )

            assert user["balance_vnd"] == 199_995
            assert user["held_balance_vnd"] == 0

        finally:
            await transactions.delete_many({"session_id": session_id})
            await wallet_holds.delete_many({"session_id": session_id})
            await sessions.delete_many({"session_id": session_id})
            await stations.delete_many({"station_id": station_id})
            await users.delete_many({"user_id": user_id})

    _test_loop.run_until_complete(scenario())
