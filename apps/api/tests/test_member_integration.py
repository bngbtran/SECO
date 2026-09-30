from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.member.service import (
    start_member_charging,
    stop_member_charging,
    verify_member_rfid,
)
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
rfid_cards = get_collection("rfid_cards")
stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
wallet_holds = get_collection("wallet_holds")

def test_valid_rfid_returns_member():
    async def scenario():
        user_id = f"test-member-{uuid4()}"
        card_id = f"RFID-{uuid4()}"
        uid = f"UID-{uuid4()}"
        now = datetime.now(UTC)

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "RFID Test Member",
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

            await rfid_cards.insert_one(
                {
                    "card_id": card_id,
                    "uid": uid,
                    "user_id": user_id,
                    "is_active": True,
                    "pending": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            card, user = await verify_member_rfid(uid=uid)

            assert card["card_id"] == card_id
            assert user["user_id"] == user_id
            assert user["role"] == "USER"

        finally:
            await rfid_cards.delete_many({"card_id": card_id})
            await users.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())


def test_unknown_rfid_is_rejected():
    async def scenario():
        with pytest.raises(ResourceNotFoundError, match="RFID card not found"):
            await verify_member_rfid(uid=f"UNKNOWN-{uuid4()}")

    TEST_LOOP.run_until_complete(scenario())


def test_inactive_or_pending_rfid_is_rejected():
    async def scenario():
        user_id = f"test-member-{uuid4()}"
        now = datetime.now(UTC)

        inactive_uid = f"INACTIVE-{uuid4()}"
        pending_uid = f"PENDING-{uuid4()}"

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Inactive RFID Test",
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

            await rfid_cards.insert_many(
                [
                    {
                        "card_id": f"RFID-{uuid4()}",
                        "uid": inactive_uid,
                        "user_id": user_id,
                        "is_active": False,
                        "pending": False,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "card_id": f"RFID-{uuid4()}",
                        "uid": pending_uid,
                        "user_id": user_id,
                        "is_active": True,
                        "pending": True,
                        "created_at": now,
                        "updated_at": now,
                    },
                ]
            )

            with pytest.raises(
                ResourceNotFoundError,
                match="RFID card not found",
            ):
                await verify_member_rfid(uid=inactive_uid)

            with pytest.raises(
                ResourceNotFoundError,
                match="RFID card not found",
            ):
                await verify_member_rfid(uid=pending_uid)

        finally:
            await rfid_cards.delete_many(
                {
                    "uid": {
                        "$in": [inactive_uid, pending_uid],
                    }
                }
            )
            await users.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())


def test_member_budget_cannot_exceed_balance():
    async def scenario():
        user_id = f"test-member-{uuid4()}"
        card_id = f"RFID-{uuid4()}"
        station_id = f"TEST-{uuid4()}"
        uid = f"UID-{uuid4()}"
        now = datetime.now(UTC)

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Budget Test Member",
                    "email": f"{user_id}@test.local",
                    "password_hash": "test",
                    "role": "USER",
                    "balance_vnd": 100_000,
                    "held_balance_vnd": 0,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await rfid_cards.insert_one(
                {
                    "card_id": card_id,
                    "uid": uid,
                    "user_id": user_id,
                    "is_active": True,
                    "pending": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "Budget Test Station",
                    "status": "READY",
                    "is_online": True,
                    "connector_lock": "UNLOCKED",
                    "current_session_id": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            with pytest.raises(
                BusinessRuleError,
                match="max_budget_vnd cannot exceed balance_vnd",
            ):
                await start_member_charging(
                    uid=uid,
                    station_id=station_id,
                    max_budget_vnd=100_001,
                )

        finally:
            await wallet_holds.delete_many(
                {"user_id": user_id}
            )
            await sessions.delete_many(
                {"user_id": user_id}
            )
            await stations.delete_many(
                {"station_id": station_id}
            )
            await rfid_cards.delete_many(
                {"card_id": card_id}
            )
            await users.delete_many(
                {"user_id": user_id}
            )

    TEST_LOOP.run_until_complete(scenario())


def test_member_start_creates_session_without_hold():
    async def scenario():
        user_id = f"test-member-{uuid4()}"
        card_id = f"RFID-{uuid4()}"
        station_id = f"TEST-{uuid4()}"
        uid = f"UID-{uuid4()}"
        now = datetime.now(UTC)

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Start Test Member",
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

            await rfid_cards.insert_one(
                {
                    "card_id": card_id,
                    "uid": uid,
                    "user_id": user_id,
                    "is_active": True,
                    "pending": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "Member Start Test Station",
                    "status": "READY",
                    "is_online": True,
                    "connector_lock": "UNLOCKED",
                    "current_session_id": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            result = await start_member_charging(
                uid=uid,
                station_id=station_id,
                max_budget_vnd=50_000,
            )

            session = result["session"]
            user = result["user"]

            assert user["user_id"] == user_id
            assert session["type"] == "MEMBER"
            assert session["user_id"] == user_id
            assert session["status"] == "ACTIVE"
            assert session["max_budget_vnd"] == 50_000

            stored_session = await sessions.find_one(
                {"session_id": session["session_id"]},
                {"_id": 0},
            )

            assert stored_session is not None
            assert stored_session["status"] == "ACTIVE"

            assert stored_session["started_at"] is not None
            assert stored_session["connector_locked"] is False
            assert stored_session["budget_mode"] == "EXPLICIT"

            # DD v1.2: no wallet hold, money is deducted when the session ends.
            assert await wallet_holds.count_documents(
                {"session_id": session["session_id"]}
            ) == 0

            stored_user = await users.find_one(
                {"user_id": user_id},
                {
                    "_id": 0,
                    "balance_vnd": 1,
                    "held_balance_vnd": 1,
                },
            )

            assert stored_user["balance_vnd"] == 200_000
            assert stored_user["held_balance_vnd"] == 0

            station = await stations.find_one(
                {"station_id": station_id},
                {
                    "_id": 0,
                    "status": 1,
                    "current_session_id": 1,
                },
            )

            assert station["status"] == "CHARGING"
            assert station["current_session_id"] == session["session_id"]

        finally:
            await transactions_cleanup(session_id=None, user_id=user_id)
            await sessions.delete_many({"user_id": user_id})
            await wallet_holds.delete_many({"user_id": user_id})
            await stations.delete_many({"station_id": station_id})
            await rfid_cards.delete_many({"card_id": card_id})
            await users.delete_many({"user_id": user_id})

    TEST_LOOP.run_until_complete(scenario())


async def transactions_cleanup(
    *,
    session_id: str | None,
    user_id: str,
) -> None:
    # Member-start cleanup helper.
    if session_id:
        await get_collection("transactions").delete_many(
            {"session_id": session_id}
        )

def test_member_stop_charging_completes_and_settles():
    async def scenario():
        user_id = f"test-member-{uuid4()}"
        card_id = f"RFID-{uuid4()}"
        station_id = f"TEST-{uuid4()}"
        uid = f"UID-{uuid4()}"
        now = datetime.now(UTC)

        session_id = None

        try:
            await users.insert_one(
                {
                    "user_id": user_id,
                    "name": "Stop Test Member",
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

            await rfid_cards.insert_one(
                {
                    "card_id": card_id,
                    "uid": uid,
                    "user_id": user_id,
                    "is_active": True,
                    "pending": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "Member Stop Test Station",
                    "status": "READY",
                    "is_online": True,
                    "connector_lock": "UNLOCKED",
                    "current_session_id": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            result = await start_member_charging(
                uid=uid,
                station_id=station_id,
                max_budget_vnd=50_000,
            )

            session_id = result["session"]["session_id"]

            session, transaction = await stop_member_charging(
                user_id=user_id,
                session_id=session_id,
            )

            assert session["session_id"] == session_id
            assert session["status"] == "COMPLETED"
            assert session["stop_reason"] == "UNPLUG"

            assert transaction is not None
            assert transaction["type"] == "CHARGING"
            assert transaction["status"] == "COMPLETED"
            assert transaction["amount_vnd"] == session["cost_vnd"]

            stored_user = await users.find_one(
                {"user_id": user_id},
                {
                    "_id": 0,
                    "balance_vnd": 1,
                    "held_balance_vnd": 1,
                },
            )

            assert stored_user["balance_vnd"] == (
                200_000 - transaction["amount_vnd"]
            )
            assert stored_user["held_balance_vnd"] == 0

            stored_station = await stations.find_one(
                {"station_id": station_id},
                {
                    "_id": 0,
                    "status": 1,
                    "current_session_id": 1,
                },
            )

            assert stored_station["status"] == "READY"
            assert stored_station["current_session_id"] is None

        finally:
            if session_id:
                await get_collection("transactions").delete_many(
                    {"session_id": session_id}
                )
                await wallet_holds.delete_many(
                    {"session_id": session_id}
                )
                await sessions.delete_many(
                    {"session_id": session_id}
                )

            await stations.delete_many(
                {"station_id": station_id}
            )
            await rfid_cards.delete_many(
                {"card_id": card_id}
            )
            await users.delete_many(
                {"user_id": user_id}
            )

    TEST_LOOP.run_until_complete(scenario())