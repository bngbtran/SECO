from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.core.dependencies import get_current_user
from app.core.exceptions import BusinessRuleError, ConflictError
from app.db.mongodb import get_collection
from app.main import app
from app.modules.member.service import set_pending_budget, start_member_charging
from app.mqtt import handlers
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
rfid_cards = get_collection("rfid_cards")
stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")


async def seed_member(*, balance_vnd: int = 100_000, station_online: bool = True) -> dict:
    now = datetime.now(UTC)
    ids = {
        "user_id": f"test-member-{uuid4()}",
        "card_id": f"RFID-{uuid4()}",
        "uid": f"UID-{uuid4()}",
        "station_id": f"TEST-{uuid4()}",
    }

    await users.insert_one(
        {
            "user_id": ids["user_id"],
            "name": "Budget Start Member",
            "email": f"{ids['user_id']}@test.local",
            "password_hash": "test",
            "role": "USER",
            "balance_vnd": balance_vnd,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    await rfid_cards.insert_one(
        {
            "card_id": ids["card_id"],
            "uid": ids["uid"],
            "user_id": ids["user_id"],
            "is_active": True,
            "pending": False,
            "created_at": now,
            "updated_at": now,
        }
    )
    await stations.insert_one(
        {
            "station_id": ids["station_id"],
            "name": "Budget Start Station",
            "status": "READY",
            "is_online": station_online,
            "connector_lock": "UNLOCKED",
            "current_session_id": None,
            "latitude": 16.0544,
            "longitude": 108.2022,
            "created_at": now,
            "updated_at": now,
        }
    )
    return ids


async def cleanup(ids: dict) -> None:
    await sessions.delete_many({"user_id": ids["user_id"]})
    await stations.delete_many({"station_id": ids["station_id"]})
    await rfid_cards.delete_many({"card_id": ids["card_id"]})
    await users.delete_many({"user_id": ids["user_id"]})


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


def test_pending_budget_cannot_exceed_balance():
    async def scenario():
        ids = await seed_member(balance_vnd=10_000)
        try:
            with pytest.raises(BusinessRuleError, match="cannot exceed balance"):
                await set_pending_budget(
                    user_id=ids["user_id"],
                    station_id=ids["station_id"],
                    max_budget_vnd=10_001,
                )
        finally:
            await cleanup(ids)

    run(scenario)


def test_rfid_start_uses_pending_budget_and_clears_it():
    async def scenario():
        ids = await seed_member()
        try:
            await set_pending_budget(
                user_id=ids["user_id"],
                station_id=ids["station_id"],
                max_budget_vnd=30_000,
            )

            result = await start_member_charging(
                uid=ids["uid"],
                station_id=ids["station_id"],
                max_budget_vnd=None,
            )

            assert result["session"]["max_budget_vnd"] == 30_000
            assert result["session"]["budget_mode"] == "EXPLICIT"

            user = await users.find_one({"user_id": ids["user_id"]}, {"_id": 0})
            assert "pending_charging_budget" not in user
        finally:
            await cleanup(ids)

    run(scenario)


def test_pending_budget_for_other_station_is_ignored():
    async def scenario():
        ids = await seed_member(balance_vnd=80_000)
        try:
            await users.update_one(
                {"user_id": ids["user_id"]},
                {
                    "$set": {
                        "pending_charging_budget": {
                            "station_id": "OTHER-STATION",
                            "max_budget_vnd": 5_000,
                            "created_at": datetime.now(UTC),
                            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
                        }
                    }
                },
            )

            result = await start_member_charging(
                uid=ids["uid"],
                station_id=ids["station_id"],
                max_budget_vnd=None,
            )

            assert result["session"]["max_budget_vnd"] == 80_000
            assert result["session"]["budget_mode"] == "BALANCE"
        finally:
            await cleanup(ids)

    run(scenario)


def test_start_rejects_card_of_another_user():
    async def scenario():
        ids = await seed_member()
        try:
            with pytest.raises(BusinessRuleError, match="does not belong"):
                await start_member_charging(
                    uid=ids["uid"],
                    station_id=ids["station_id"],
                    max_budget_vnd=None,
                    expected_user_id="someone-else",
                )
        finally:
            await cleanup(ids)

    run(scenario)


def test_start_rejects_offline_station():
    async def scenario():
        ids = await seed_member(station_online=False)
        try:
            with pytest.raises(ConflictError, match="offline"):
                await start_member_charging(
                    uid=ids["uid"],
                    station_id=ids["station_id"],
                    max_budget_vnd=None,
                )
        finally:
            await cleanup(ids)

    run(scenario)


def test_mqtt_rfid_verify_and_session_start_publish_response(monkeypatch):
    async def scenario():
        ids = await seed_member()
        published = []

        async def fake_publish(topic, payload):
            published.append((topic, payload))

        async def fake_broadcast(data):
            pass

        monkeypatch.setattr(handlers, "publish", fake_publish)
        monkeypatch.setattr(handlers.manager, "broadcast", fake_broadcast)

        try:
            await handlers.handle_message(
                f"evcs/{ids['station_id']}/req",
                f'{{"type":"RFID_VERIFY","uid":"{ids["uid"]}","request_id":"r1"}}'.encode(),
            )
            topic, payload = published[-1]
            assert topic == f"evcs/{ids['station_id']}/res"
            assert payload["ok"] is True
            assert payload["request_id"] == "r1"
            assert payload["name"] == "Budget Start Member"
            assert payload["balance_vnd"] == 100_000

            await handlers.handle_message(
                f"evcs/{ids['station_id']}/req",
                f'{{"type":"SESSION_START","uid":"{ids["uid"]}","max_budget_vnd":20000}}'.encode(),
            )
            topic, payload = published[-1]
            assert payload["ok"] is True
            assert payload["type"] == "SESSION_START"
            assert payload["session"]["status"] == "ACTIVE"
            assert payload["session"]["max_budget_vnd"] == 20_000
            assert payload["session"]["energy_kwh"] == 0.0

            station = await stations.find_one({"station_id": ids["station_id"]}, {"_id": 0})
            assert station["status"] == "CHARGING"
            assert station["current_session_id"] == payload["session"]["session_id"]

            await handlers.handle_message(
                f"evcs/{ids['station_id']}/req",
                b'{"type":"RFID_VERIFY","uid":"UNKNOWN-CARD"}',
            )
            _, payload = published[-1]
            assert payload["ok"] is False
            assert "not found" in payload["error"]
        finally:
            await cleanup(ids)

    run(scenario)


def test_heartbeat_marks_station_online():
    async def scenario():
        ids = await seed_member(station_online=False)
        try:
            await handlers.handle_message(f"evcs/{ids['station_id']}/hb", b"{}")
            station = await stations.find_one({"station_id": ids["station_id"]}, {"_id": 0})
            assert station["is_online"] is True
            assert station["last_heartbeat_at"] is not None
        finally:
            await cleanup(ids)

    run(scenario)


def test_member_budget_and_start_api():
    async def scenario():
        ids = await seed_member()

        async def fake_current_user():
            return {
                "user_id": ids["user_id"],
                "name": "Budget Start Member",
                "email": "x@test.local",
                "role": "USER",
                "is_active": True,
                "balance_vnd": 100_000,
            }

        app.dependency_overrides[get_current_user] = fake_current_user

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.put(
                    "/api/v1/member/charging/budget",
                    json={"station_id": ids["station_id"], "max_budget_vnd": 999_999},
                )
                assert response.status_code == 400

                response = await client.put(
                    "/api/v1/member/charging/budget",
                    json={"station_id": ids["station_id"], "max_budget_vnd": 25_000},
                )
                assert response.status_code == 200
                assert response.json()["max_budget_vnd"] == 25_000

                response = await client.get("/api/v1/member/charging/budget")
                assert response.status_code == 200

                response = await client.post(
                    "/api/v1/member/charging/start",
                    json={"uid": ids["uid"], "station_id": ids["station_id"]},
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["max_budget_vnd"] == 25_000
                assert body["status"] == "ACTIVE"

                response = await client.get(f"/api/v1/member/charging/{body['session_id']}")
                assert response.status_code == 200
                assert response.json()["cost_vnd"] == 0

                response = await client.post(
                    "/api/v1/member/charging/start",
                    json={"uid": ids["uid"], "station_id": ids["station_id"]},
                )
                assert response.status_code == 409

                response = await client.get("/api/v1/stations")
                assert response.status_code == 200
                station = next(
                    s for s in response.json() if s["station_id"] == ids["station_id"]
                )
                assert station["status"] == "CHARGING"
                assert station["latitude"] == 16.0544
                assert station["longitude"] == 108.2022
        finally:
            app.dependency_overrides.clear()
            await cleanup(ids)

    run(scenario)
