import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from bson.decimal128 import Decimal128

from app.core.exceptions import BusinessRuleError
from app.db.indexes import create_indexes
from app.db.mongodb import get_collection
from app.main import app
from app.modules.guest import realtime as guest_realtime
from app.modules.guest.service import (
    create_payment_intent,
    get_guest_session,
    pay_payment,
    start_guest_charging,
    stop_guest_session,
)
from app.mqtt import handlers
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
payment_intents = get_collection("payment_intents")
transactions = get_collection("transactions")


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


@pytest.fixture(scope="module", autouse=True)
def indexes():
    # The one-PENDING / one-PAID payment guarantees rely on these unique indexes.
    TEST_LOOP.run_until_complete(create_indexes())


@pytest.fixture
def captured(monkeypatch):
    """Capture station commands, MQTT responses and WebSocket broadcasts."""
    data = {"commands": [], "responses": [], "broadcasts": []}

    async def fake_command(station_id, payload):
        data["commands"].append(payload)

    async def fake_publish(topic, payload):
        data["responses"].append(payload)

    async def fake_broadcast(payload):
        data["broadcasts"].append(payload)

    monkeypatch.setattr(guest_realtime, "publish_station_command", fake_command)
    monkeypatch.setattr(handlers, "publish", fake_publish)
    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    return data


def commands(captured) -> list[str]:
    return [c["command"] for c in captured["commands"]]


async def seed_station(**overrides) -> str:
    station_id = f"TEST-{uuid4()}"
    now = datetime.now(UTC)
    await stations.insert_one(
        {
            "station_id": station_id,
            "name": "Guest Station",
            "status": "READY",
            "is_online": True,
            "connector_lock": "UNLOCKED",
            "current_session_id": None,
            "created_at": now,
            "updated_at": now,
            **overrides,
        }
    )
    return station_id


async def seed_guest_session(
    *,
    started_seconds_ago: int,
    max_budget_vnd: int | None = None,
) -> tuple[str, str]:
    station_id = await seed_station()
    session = await start_guest_charging(station_id=station_id, max_budget_vnd=max_budget_vnd)
    started_at = datetime.now(UTC) - timedelta(seconds=started_seconds_ago)
    await sessions.update_one(
        {"session_id": session["session_id"]},
        {"$set": {"started_at": started_at}},
    )
    return station_id, session["session_id"]


async def cleanup(station_id: str) -> None:
    session_ids = [
        s["session_id"]
        async for s in sessions.find({"station_id": station_id}, {"session_id": 1})
    ]
    await transactions.delete_many({"session_id": {"$in": session_ids}})
    await payment_intents.delete_many({"session_id": {"$in": session_ids}})
    await sessions.delete_many({"station_id": station_id})
    await stations.delete_many({"station_id": station_id})


async def load(station_id: str, session_id: str) -> tuple[dict, dict]:
    session = await sessions.find_one({"session_id": session_id}, {"_id": 0})
    station = await stations.find_one({"station_id": station_id}, {"_id": 0})
    return session, station


def api() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# UC-G01 --------------------------------------------------------------------


def test_station_availability_for_guests():
    async def scenario():
        ready = await seed_station()
        offline = await seed_station(is_online=False)
        broken = await seed_station(status="ERROR")
        waiting, waiting_session = await seed_guest_session(started_seconds_ago=65)
        charging, _ = await seed_guest_session(started_seconds_ago=0)
        try:
            await stop_guest_session(session_id=waiting_session, stop_reason="UNPLUG")

            async with api() as http:
                response = await http.get("/api/v1/stations")
            by_id = {s["station_id"]: s["availability"] for s in response.json()}

            assert by_id[ready] == "AVAILABLE"
            assert by_id[offline] == "OFFLINE"
            assert by_id[broken] == "ERROR"
            assert by_id[waiting] == "WAITING_PAYMENT"
            assert by_id[charging] == "CHARGING"
        finally:
            for station_id in (ready, offline, broken, waiting, charging):
                await cleanup(station_id)

    run(scenario)


# UC-G02 / G03 --------------------------------------------------------------


@pytest.mark.parametrize("budget", [0, -5, 1_000_001])
def test_guest_budget_is_validated(budget):
    async def scenario():
        station_id = await seed_station()
        try:
            with pytest.raises(BusinessRuleError):
                await start_guest_charging(station_id=station_id, max_budget_vnd=budget)
        finally:
            await cleanup(station_id)

    run(scenario)


def test_guest_start_via_mqtt_without_rfid(captured):
    async def scenario():
        station_id = await seed_station()
        try:
            await handle_message(
                f"evcs/{station_id}/req",
                b'{"type":"GUEST_START","max_budget_vnd":20000,"request_id":"g1"}',
            )
            response = captured["responses"][-1]
            assert response["ok"] is True
            assert response["request_id"] == "g1"
            assert response["session"]["max_budget_vnd"] == 20_000

            session, station = await load(station_id, response["session"]["session_id"])
            assert session["type"] == "GUEST"
            assert session["user_id"] is None
            assert session["budget_mode"] == "EXPLICIT"
            assert station["status"] == "CHARGING"
            assert captured["broadcasts"][-1]["type"] == "guest_session_updated"
            assert captured["broadcasts"][-1]["event"] == "STARTED"

            # The station is busy now.
            await handle_message(f"evcs/{station_id}/req", b'{"type":"GUEST_START"}')
            assert captured["responses"][-1]["ok"] is False

            # A budget is optional.
            other = await seed_station()
            await handle_message(f"evcs/{other}/req", b'{"type":"GUEST_START"}')
            response = captured["responses"][-1]
            assert response["ok"] is True
            assert response["session"]["max_budget_vnd"] is None
            await cleanup(other)
        finally:
            await cleanup(station_id)

    run(scenario)


# UC-G04 --------------------------------------------------------------------


def test_guest_realtime_telemetry_and_live_view(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(
            started_seconds_ago=65,
            max_budget_vnd=1_000,
        )
        try:
            await handle_message(
                f"evcs/{station_id}/telemetry",
                b'{"power_kw":0.15,"voltage_v":12.0,"current_a":1.25,"temperature_c":30.5}',
            )
            update = captured["broadcasts"][-1]
            assert update["type"] == "guest_session_updated"
            assert update["event"] == "TELEMETRY"
            assert "user_id" not in update
            assert update["cost_vnd"] == 10
            assert update["remaining_budget_vnd"] == 990
            assert update["temperature_c"] == 30.5

            async with api() as http:
                response = await http.get(f"/api/v1/guest/stations/{station_id}/session")
            live = response.json()
            assert response.status_code == 200
            assert live["session_id"] == session_id
            assert live["status"] == "ACTIVE"
            assert live["cost_vnd"] == 10
            assert live["duration_seconds"] >= 65
            assert live["payment"] is None
        finally:
            await cleanup(station_id)

    run(scenario)


# UC-G05 / G06 / G07 --------------------------------------------------------


def test_unplug_locks_connector_and_shows_qr(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=65)
        try:
            await handle_message(f"evcs/{station_id}/event", b'{"event":"UNPLUGGED"}')

            session, station = await load(station_id, session_id)
            assert session["status"] == "WAITING_PAYMENT"
            assert session["stop_reason"] == "UNPLUG"
            assert session["cost_vnd"] == 10
            assert session["connector_locked"] is True
            assert station["current_session_id"] == session_id

            assert commands(captured) == ["STOP", "LOCK", "PAYMENT_QR"]
            qr = captured["commands"][-1]
            assert qr["amount_vnd"] == 10
            assert qr["status"] == "PENDING"
            assert qr["qr_payload"].endswith(qr["payment_id"])
            assert captured["broadcasts"][-1]["event"] == "WAITING_PAYMENT"

            # UC-G06: the lock state follows the station's acknowledgement.
            await handle_message(f"evcs/{station_id}/cmd_ack", b'{"command":"LOCK","ok":true}')
            _, station = await load(station_id, session_id)
            assert station["connector_lock"] == "LOCKED"

            # No new session while the guest still owes money.
            await handle_message(f"evcs/{station_id}/req", b'{"type":"GUEST_START"}')
            assert captured["responses"][-1]["ok"] is False

            # A late unplug does not create a second QR.
            await handle_message(f"evcs/{station_id}/event", b'{"event":"UNPLUGGED"}')
            assert await payment_intents.count_documents({"session_id": session_id}) == 1
        finally:
            await cleanup(station_id)

    run(scenario)


def test_free_guest_session_completes_without_payment(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=5)
        try:
            await handle_message(f"evcs/{station_id}/event", b'{"event":"UNPLUGGED"}')

            session, station = await load(station_id, session_id)
            assert session["status"] == "COMPLETED"
            assert session["cost_vnd"] == 0
            assert station["status"] == "READY"
            assert station["current_session_id"] is None
            assert commands(captured) == ["STOP"]
            assert await payment_intents.count_documents({"session_id": session_id}) == 0
        finally:
            await cleanup(station_id)

    run(scenario)


def test_budget_reached_stops_guest_session(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(
            started_seconds_ago=125,
            max_budget_vnd=10,
        )
        try:
            await guest_realtime.tick_guest_sessions()

            session, _ = await load(station_id, session_id)
            assert session["status"] == "WAITING_PAYMENT"
            assert session["stop_reason"] == "MAX_BUDGET"
            assert session["cost_vnd"] == 10
            assert commands(captured) == ["STOP", "LOCK", "PAYMENT_QR"]
        finally:
            await cleanup(station_id)

    run(scenario)


def test_estop_keeps_station_in_error_after_payment(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=35)
        try:
            await handle_message(f"evcs/{station_id}/event", b'{"event":"ESTOP"}')

            session, station = await load(station_id, session_id)
            assert session["status"] == "WAITING_PAYMENT"
            assert session["stop_reason"] == "E_STOP"
            assert station["status"] == "ERROR"

            payment = await payment_intents.find_one({"session_id": session_id})
            await pay_payment(payment["payment_id"])

            session, station = await load(station_id, session_id)
            assert session["status"] == "COMPLETED"
            assert station["status"] == "ERROR"
            assert station["current_session_id"] is None
        finally:
            await cleanup(station_id)

    run(scenario)


def test_offline_station_stops_guest_session(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=35)
        try:
            await stations.update_one({"station_id": station_id}, {"$set": {"is_online": False}})
            await guest_realtime.tick_guest_sessions()

            session, station = await load(station_id, session_id)
            assert session["status"] == "WAITING_PAYMENT"
            assert session["stop_reason"] == "SYSTEM_ERROR"
            assert station["status"] == "ERROR"
        finally:
            await cleanup(station_id)

    run(scenario)


# UC-G08 / G09 / G11 --------------------------------------------------------


def test_sandbox_payment_unlocks_and_returns_receipt(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=65)
        try:
            result = await stop_guest_session(session_id=session_id, stop_reason="UNPLUG")
            payment_id = result["payment"]["payment_id"]

            async with api() as http:
                # UC-G08: page opened from the QR.
                response = await http.get(f"/api/v1/guest/payments/{payment_id}")
                page = response.json()
                assert response.status_code == 200
                assert page["amount_vnd"] == 10
                assert page["status"] == "PENDING"
                assert page["station_name"] == "Guest Station"
                assert page["session_id"] == session_id

                # UC-G09: pay.
                response = await http.post(f"/api/v1/guest/payments/{payment_id}/pay")
                receipt = response.json()
                assert response.status_code == 200, response.text
                assert receipt["status"] == "COMPLETED"
                assert receipt["payment"]["status"] == "PAID"
                assert receipt["transaction_id"] is not None
                assert "UNLOCK" in commands(captured)
                assert captured["broadcasts"][-1]["event"] == "PAID"

                session, station = await load(station_id, session_id)
                assert session["connector_locked"] is False
                assert station["status"] == "READY"
                assert station["current_session_id"] is None

                transaction = await transactions.find_one({"session_id": session_id})
                assert transaction["type"] == "GUEST_PAYMENT"
                assert transaction["user_id"] is None
                assert transaction["amount_vnd"] == 10

                # Paying twice is harmless.
                response = await http.post(f"/api/v1/guest/payments/{payment_id}/pay")
                assert response.status_code == 200
                assert await transactions.count_documents({"session_id": session_id}) == 1
                assert commands(captured).count("UNLOCK") == 1

                # UC-G11: receipt.
                response = await http.get(f"/api/v1/guest/sessions/{session_id}")
                receipt = response.json()
                assert receipt["energy_kwh"] == "0.0025"
                assert receipt["cost_vnd"] == 10
                assert receipt["stop_reason"] == "UNPLUG"
                assert receipt["duration_seconds"] >= 65
                assert receipt["payment"]["paid_at"] is not None
        finally:
            await cleanup(station_id)

    run(scenario)


def test_concurrent_payments_charge_once(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=65)
        try:
            result = await stop_guest_session(session_id=session_id, stop_reason="UNPLUG")
            payment_id = result["payment"]["payment_id"]

            results = await asyncio.gather(pay_payment(payment_id), pay_payment(payment_id))

            assert sorted(r["paid_now"] for r in results) == [False, True]
            assert await transactions.count_documents({"session_id": session_id}) == 1
        finally:
            await cleanup(station_id)

    run(scenario)


# UC-G10 --------------------------------------------------------------------


def test_expired_or_cancelled_qr_keeps_lock_and_can_be_renewed(captured):
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=65)
        try:
            result = await stop_guest_session(session_id=session_id, stop_reason="UNPLUG")
            first = result["payment"]["payment_id"]

            await payment_intents.update_one(
                {"payment_id": first},
                {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
            )
            await guest_realtime.tick_payment_expiry()

            expired = await payment_intents.find_one({"payment_id": first})
            assert expired["status"] == "EXPIRED"
            assert captured["commands"][-1] == {
                "command": "PAYMENT_STATUS",
                "session_id": session_id,
                "payment_id": first,
                "status": "EXPIRED",
            }

            session, station = await load(station_id, session_id)
            assert session["status"] == "WAITING_PAYMENT"
            assert session["connector_locked"] is True
            assert station["current_session_id"] == session_id

            async with api() as http:
                response = await http.post(f"/api/v1/guest/payments/{first}/pay")
                assert response.status_code == 409

                # The station asks for a new QR.
                await handle_message(
                    f"evcs/{station_id}/req",
                    f'{{"type":"GUEST_PAYMENT_REQUEST","session_id":"{session_id}"}}'.encode(),
                )
                second = captured["responses"][-1]["payment"]["payment_id"]
                assert second != first

                # Asking again returns the same open QR.
                response = await http.post(f"/api/v1/guest/sessions/{session_id}/payments")
                assert response.json()["payment_id"] == second

                response = await http.post(f"/api/v1/guest/payments/{second}/cancel")
                assert response.json()["status"] == "CANCELLED"

                response = await http.post(f"/api/v1/guest/sessions/{session_id}/payments")
                third = response.json()["payment_id"]
                assert third not in (first, second)

                response = await http.post(f"/api/v1/guest/payments/{third}/pay")
                assert response.status_code == 200

                # Once paid, no further QR can be created.
                response = await http.post(f"/api/v1/guest/sessions/{session_id}/payments")
                assert response.status_code == 400

            statuses = sorted(
                [p["status"] async for p in payment_intents.find({"session_id": session_id})]
            )
            assert statuses == ["CANCELLED", "EXPIRED", "PAID"]
        finally:
            await cleanup(station_id)

    run(scenario)


def test_only_one_open_qr_per_session():
    async def scenario():
        station_id, session_id = await seed_guest_session(started_seconds_ago=65)
        try:
            await stop_guest_session(session_id=session_id, stop_reason="UNPLUG")
            await payment_intents.delete_many({"session_id": session_id})
            session = await get_guest_session(session_id)

            results = await asyncio.gather(*(create_payment_intent(session) for _ in range(3)))

            assert len({p["payment_id"] for p in results}) == 1
            assert await payment_intents.count_documents({"session_id": session_id}) == 1
        finally:
            await cleanup(station_id)

    run(scenario)


def test_unknown_guest_ids_return_404():
    async def scenario():
        async with api() as http:
            assert (await http.get(f"/api/v1/guest/sessions/{uuid4()}")).status_code == 404
            assert (await http.get(f"/api/v1/guest/payments/{uuid4()}")).status_code == 404
            response = await http.post(f"/api/v1/guest/payments/{uuid4()}/pay")
            assert response.status_code == 404

    run(scenario)


def test_member_session_is_not_exposed_as_guest():
    async def scenario():
        station_id = await seed_station(status="CHARGING")
        session_id = str(uuid4())
        now = datetime.now(UTC)
        await sessions.insert_one(
            {
                "session_id": session_id,
                "station_id": station_id,
                "type": "MEMBER",
                "user_id": "someone",
                "status": "ACTIVE",
                "max_budget_vnd": 100,
                "energy_kwh": Decimal128("0"),
                "power_kw": Decimal128("0.15"),
                "price_per_kwh_vnd": 4000,
                "cost_vnd": 0,
                "stop_reason": None,
                "started_at": now,
                "ended_at": None,
                "connector_locked": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await stations.update_one(
            {"station_id": station_id},
            {"$set": {"current_session_id": session_id}},
        )
        try:
            async with api() as http:
                response = await http.get(f"/api/v1/guest/sessions/{session_id}")
                assert response.status_code == 404
                response = await http.get(f"/api/v1/guest/stations/{station_id}/session")
                assert response.status_code == 404
        finally:
            await cleanup(station_id)

    run(scenario)
