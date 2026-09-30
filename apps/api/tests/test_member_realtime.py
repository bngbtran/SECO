from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from bson.decimal128 import Decimal128

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.db.mongodb import get_collection
from app.main import app
from app.modules.member import realtime
from app.modules.member.service import process_member_session, refresh_session_usage
from app.mqtt.handlers import handle_message
from app.websocket.manager import ConnectionManager, manager
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
transactions = get_collection("transactions")

settings = get_settings()


async def seed_active_session(
    *,
    started_seconds_ago: int,
    max_budget_vnd: int = 50_000,
    budget_mode: str = "EXPLICIT",
    balance_vnd: int = 100_000,
    station_online: bool = True,
) -> dict:
    now = datetime.now(UTC)
    ids = {
        "user_id": f"test-member-{uuid4()}",
        "station_id": f"TEST-{uuid4()}",
        "session_id": str(uuid4()),
    }

    await users.insert_one(
        {
            "user_id": ids["user_id"],
            "name": "Realtime Member",
            "email": f"{ids['user_id']}@test.local",
            "password_hash": "test",
            "role": "USER",
            "balance_vnd": balance_vnd,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    await stations.insert_one(
        {
            "station_id": ids["station_id"],
            "name": "Realtime Station",
            "status": "CHARGING",
            "is_online": station_online,
            "connector_lock": "UNLOCKED",
            "current_session_id": ids["session_id"],
            "created_at": now,
            "updated_at": now,
        }
    )
    started_at = now - timedelta(seconds=started_seconds_ago)
    await sessions.insert_one(
        {
            "session_id": ids["session_id"],
            "station_id": ids["station_id"],
            "type": "MEMBER",
            "user_id": ids["user_id"],
            "status": "ACTIVE",
            "budget_mode": budget_mode,
            "max_budget_vnd": max_budget_vnd,
            "energy_kwh": Decimal128("0"),
            "power_kw": Decimal128("0.15"),
            "price_per_kwh_vnd": 4000,
            "cost_vnd": 0,
            "stop_reason": None,
            "started_at": started_at,
            "ended_at": None,
            "connector_locked": False,
            "created_at": started_at,
            "updated_at": started_at,
        }
    )
    return ids


async def cleanup(ids: dict) -> None:
    await transactions.delete_many({"session_id": ids["session_id"]})
    await sessions.delete_many({"session_id": ids["session_id"]})
    await stations.delete_many({"station_id": ids["station_id"]})
    await users.delete_many({"user_id": ids["user_id"]})


async def load(ids: dict) -> tuple[dict, dict, dict]:
    session = await sessions.find_one({"session_id": ids["session_id"]}, {"_id": 0})
    station = await stations.find_one({"station_id": ids["station_id"]}, {"_id": 0})
    user = await users.find_one({"user_id": ids["user_id"]}, {"_id": 0})
    return session, station, user


@pytest.fixture
def captured(monkeypatch):
    """Capture STOP commands and WebSocket broadcasts instead of sending them."""
    data = {"commands": [], "broadcasts": []}

    async def fake_command(station_id, payload):
        data["commands"].append((station_id, payload))

    async def fake_broadcast(payload):
        data["broadcasts"].append(payload)

    monkeypatch.setattr(realtime, "publish_station_command", fake_command)
    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    return data


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


def test_usage_follows_elapsed_ticks():
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=95)
        try:
            session = (await load(ids))[0]
            session, exhausted = await refresh_session_usage(session)

            # 95 s -> 3 whole ticks of 0.00125 kWh = 5 VND each.
            assert session["energy_kwh"].to_decimal() == Decimal("0.00375")
            assert session["cost_vnd"] == 15
            assert exhausted is False

            # Idempotent: refreshing again in the same tick changes nothing.
            session, _ = await refresh_session_usage(session)
            assert session["cost_vnd"] == 15
        finally:
            await cleanup(ids)

    run(scenario)


@pytest.mark.parametrize(
    ("budget_mode", "expected_reason"),
    [("EXPLICIT", "MAX_BUDGET"), ("BALANCE", "INSUFFICIENT_BALANCE")],
)
def test_budget_auto_stop_settles_capped_cost(budget_mode, expected_reason):
    async def scenario():
        ids = await seed_active_session(
            started_seconds_ago=125,
            max_budget_vnd=10,
            budget_mode=budget_mode,
        )
        try:
            result = await process_member_session((await load(ids))[0])

            assert result["auto_stopped"] is True
            assert result["stop_reason"] == expected_reason
            assert result["transaction"]["amount_vnd"] == 10

            session, station, user = await load(ids)
            assert session["status"] == "COMPLETED"
            assert session["cost_vnd"] == 10
            assert session["stop_reason"] == expected_reason
            assert station["status"] == "READY"
            assert station["current_session_id"] is None
            assert user["balance_vnd"] == 100_000 - 10
        finally:
            await cleanup(ids)

    run(scenario)


def test_unplug_event_stops_bills_and_notifies(captured):
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=65)
        try:
            await handle_message(f"evcs/{ids['station_id']}/event", b'{"event":"UNPLUGGED"}')

            session, station, user = await load(ids)
            assert session["status"] == "COMPLETED"
            assert session["stop_reason"] == "UNPLUG"
            assert session["cost_vnd"] == 10
            assert station["status"] == "READY"
            assert user["balance_vnd"] == 100_000 - 10

            assert captured["commands"] == [
                (
                    ids["station_id"],
                    {"command": "STOP", "session_id": ids["session_id"], "reason": "UNPLUG"},
                )
            ]
            stopped = captured["broadcasts"][-1]
            assert stopped["event"] == "STOPPED"
            assert stopped["user_id"] == ids["user_id"]
            assert stopped["amount_vnd"] == 10
            assert stopped["balance_after_vnd"] == 100_000 - 10

            # A second unplug for the same station does nothing.
            await handle_message(f"evcs/{ids['station_id']}/event", b'{"event":"UNPLUGGED"}')
            assert await transactions.count_documents({"session_id": ids["session_id"]}) == 1
        finally:
            await cleanup(ids)

    run(scenario)


def test_estop_event_puts_station_in_error(captured):
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=5)
        try:
            await handle_message(f"evcs/{ids['station_id']}/event", b'{"event":"ESTOP"}')

            session, station, _ = await load(ids)
            assert session["stop_reason"] == "E_STOP"
            assert station["status"] == "ERROR"
        finally:
            await cleanup(ids)

    run(scenario)


def test_telemetry_broadcasts_live_session(captured):
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=65, max_budget_vnd=1_000)
        try:
            await handle_message(
                f"evcs/{ids['station_id']}/telemetry",
                b'{"power_kw":0.15,"voltage_v":12.1,"current_a":1.2,"temperature_c":31.0}',
            )

            update = captured["broadcasts"][-1]
            assert update["event"] == "TELEMETRY"
            assert update["user_id"] == ids["user_id"]
            assert update["status"] == "CHARGING"
            assert update["energy_kwh"] == 0.0025
            assert update["cost_vnd"] == 10
            assert update["max_budget_vnd"] == 1_000
            assert update["remaining_budget_vnd"] == 990
            assert update["temperature_c"] == 31.0
            assert update["duration_seconds"] >= 65
        finally:
            await cleanup(ids)

    run(scenario)


def test_ticker_bills_without_telemetry_and_stops_offline_station(captured):
    async def scenario():
        online = await seed_active_session(started_seconds_ago=35)
        offline = await seed_active_session(started_seconds_ago=35, station_online=False)
        try:
            await realtime.tick_active_sessions()

            session, _, _ = await load(online)
            assert session["status"] == "ACTIVE"
            assert session["cost_vnd"] == 5
            assert any(
                b["event"] == "TICK" and b["session_id"] == online["session_id"]
                for b in captured["broadcasts"]
            )

            session, station, _ = await load(offline)
            assert session["status"] == "COMPLETED"
            assert session["stop_reason"] == "SYSTEM_ERROR"
            assert station["status"] == "ERROR"
        finally:
            await cleanup(online)
            await cleanup(offline)

    run(scenario)


def test_websocket_only_delivers_own_session_events():
    async def scenario():
        class FakeWebSocket:
            def __init__(self):
                self.messages = []

            async def accept(self):
                pass

            async def send_json(self, data):
                self.messages.append(data)

        local = ConnectionManager()
        anonymous, alice, bob, admin = (FakeWebSocket() for _ in range(4))
        await local.connect(anonymous)
        await local.connect(alice, {"user_id": "alice", "role": "USER"})
        await local.connect(bob, {"user_id": "bob", "role": "USER"})
        await local.connect(admin, {"user_id": "root", "role": "ADMIN"})

        await local.broadcast({"type": "member_session_updated", "user_id": "alice"})
        await local.broadcast({"type": "station_status", "station_id": "EVCS-01"})

        assert [m["type"] for m in alice.messages] == ["member_session_updated", "station_status"]
        assert [m["type"] for m in admin.messages] == ["member_session_updated", "station_status"]
        assert [m["type"] for m in bob.messages] == ["station_status"]
        assert [m["type"] for m in anonymous.messages] == ["station_status"]

    run(scenario)


def test_member_current_stop_result_and_history_api(captured, monkeypatch):
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=65)

        async def fake_current_user():
            return {
                "user_id": ids["user_id"],
                "name": "Realtime Member",
                "email": "x@test.local",
                "role": "USER",
                "is_active": True,
                "balance_vnd": 100_000,
            }

        app.dependency_overrides[get_current_user] = fake_current_user

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # UC-U10: live snapshot of the running session.
                response = await client.get("/api/v1/member/charging/current")
                assert response.status_code == 200, response.text
                live = response.json()
                assert live["session_id"] == ids["session_id"]
                assert live["status"] == "ACTIVE"
                assert live["cost_vnd"] == 10
                assert live["remaining_budget_vnd"] == 50_000 - 10

                # UC-U11: stop from the app sends STOP to the station.
                response = await client.post(f"/api/v1/member/charging/{ids['session_id']}/stop")
                assert response.status_code == 200, response.text
                assert captured["commands"][-1][1]["command"] == "STOP"

                # UC-U12: result screen.
                response = await client.get(f"/api/v1/member/charging/{ids['session_id']}")
                result = response.json()
                assert result["status"] == "COMPLETED"
                assert result["stop_reason"] == "UNPLUG"
                assert result["cost_vnd"] == 10
                assert result["transaction_amount_vnd"] == 10
                assert result["balance_after_vnd"] == 100_000 - 10
                assert result["balance_vnd"] == 100_000 - 10
                assert result["duration_seconds"] >= 65

                response = await client.get("/api/v1/member/charging/current")
                assert response.status_code == 404

                # UC-U15: history shows the finished session with its station.
                response = await client.get("/api/v1/member/sessions")
                history = response.json()
                assert history[0]["session_id"] == ids["session_id"]
                assert history[0]["station_name"] == "Realtime Station"
                assert history[0]["duration_seconds"] >= 65

                # UC-U14: demo top-up respects the feature flag and the limit.
                monkeypatch.setattr(settings, "demo_topup_enabled", False)
                response = await client.post("/api/v1/member/topup", json={"amount_vnd": 1_000})
                assert response.status_code == 403

                monkeypatch.setattr(settings, "demo_topup_enabled", True)
                response = await client.post(
                    "/api/v1/member/topup",
                    json={"amount_vnd": settings.demo_topup_max_vnd + 1},
                )
                assert response.status_code == 400
        finally:
            app.dependency_overrides.clear()
            await transactions.delete_many({"user_id": ids["user_id"]})
            await cleanup(ids)

    run(scenario)


def test_rfid_result_is_not_broadcast_to_websocket(captured, monkeypatch):
    async def scenario():
        ids = await seed_active_session(started_seconds_ago=0)
        try:
            async def fake_publish(topic, payload):
                pass

            monkeypatch.setattr("app.mqtt.handlers.publish", fake_publish)
            await handle_message(
                f"evcs/{ids['station_id']}/req",
                b'{"type":"RFID_VERIFY","uid":"UNKNOWN"}',
            )
            # Main no longer relays raw MQTT results, and RFID replies go to MQTT only.
            assert captured["broadcasts"] == []
        finally:
            await cleanup(ids)

    run(scenario)
