from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db.mongodb import get_collection
from app.modules.admin import stations as admin_stations
from app.modules.guest import realtime as guest_realtime
from app.mqtt import handlers
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP
from tests.test_admin import Cleanup, seed_station, seed_user

stations = get_collection("charging_stations")
rfid_cards = get_collection("rfid_cards")
maintenance_records = get_collection("maintenance_records")
sessions = get_collection("charging_sessions")


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


@pytest.fixture
def station_io(monkeypatch):
    data = {"res": [], "cmd": []}

    async def fake_publish(topic, payload):
        data["res"].append(payload)

    async def fake_command(station_id, payload):
        data["cmd"].append(payload)
        return True

    async def fake_broadcast(payload):
        pass

    monkeypatch.setattr(handlers, "publish", fake_publish)
    monkeypatch.setattr(admin_stations, "publish_station_command", fake_command)
    monkeypatch.setattr(guest_realtime, "publish_station_command", fake_command)
    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    return data


async def req(station_id: str, body: str) -> None:
    await handle_message(f"evcs/{station_id}/req", body.encode())


async def give_card(user: dict, clean: Cleanup) -> str:
    uid = f"K{uuid4().hex[:7]}".upper()
    card_id = f"CARD-{uid}"
    await rfid_cards.insert_one(
        {
            "card_id": card_id,
            "uid": uid,
            "user_id": user["user_id"],
            "is_active": True,
            "pending": False,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    clean.card_ids.add(card_id)
    return uid


def test_technician_takes_station_from_error_to_maintenance_and_back(station_io):
    async def scenario():
        clean = Cleanup()
        tech = await seed_user("TECHNICIAN", 0)
        member = await seed_user("USER")
        station_id = await seed_station()
        clean.user_ids |= {tech["user_id"], member["user_id"]}
        clean.station_ids.add(station_id)
        tech_uid = await give_card(tech, clean)
        member_uid = await give_card(member, clean)
        try:
            await handle_message(f"evcs/{station_id}/event", b'{"event":"ESTOP"}')
            assert (await stations.find_one({"station_id": station_id}))["status"] == "ERROR"

            await req(station_id, f'{{"type":"TECHNICIAN_VERIFY","uid":"{member_uid}"}}')
            assert station_io["res"][-1] == {"type": "TECHNICIAN_VERIFY", "ok": False, "error": "NOT_TECHNICIAN"}

            await req(station_id, f'{{"type":"TECHNICIAN_VERIFY","uid":"{tech_uid}"}}')
            assert station_io["res"][-1]["ok"] is True
            assert station_io["res"][-1]["name"] == tech["name"]
            assert station_io["res"][-1]["station_status"] == "ERROR"

            # Exiting before entering is refused.
            await req(station_id, f'{{"type":"TECHNICIAN_ACTION","uid":"{tech_uid}","action":"EXIT_MAINTENANCE"}}')
            assert station_io["res"][-1]["ok"] is False

            await req(station_id, f'{{"type":"TECHNICIAN_ACTION","uid":"{tech_uid}","action":"ENTER_MAINTENANCE"}}')
            assert station_io["res"][-1]["ok"] is True
            assert station_io["res"][-1]["station_status"] == "MAINTENANCE"
            assert station_io["cmd"][-1]["command"] == "MAINTENANCE"
            record = await maintenance_records.find_one({"station_id": station_id})
            assert record["technician_id"] == tech["user_id"]
            assert record["status"] == "IN_PROGRESS"

            await req(station_id, f'{{"type":"TECHNICIAN_ACTION","uid":"{tech_uid}","action":"EXIT_MAINTENANCE"}}')
            assert station_io["res"][-1]["station_status"] == "READY"
            assert station_io["cmd"][-1]["command"] == "READY"
            record = await maintenance_records.find_one({"station_id": station_id})
            assert record["status"] == "COMPLETED"
        finally:
            await clean()

    run(scenario)


def test_station_sync_restores_a_guest_waiting_for_payment(station_io):
    async def scenario():
        clean = Cleanup()
        station_id = await seed_station()
        clean.station_ids.add(station_id)
        try:
            await req(station_id, '{"type":"STATION_SYNC"}')
            synced = station_io["res"][-1]
            assert synced["ok"] is True
            assert synced["status"] == "READY"
            assert synced["session"] is None

            await req(station_id, '{"type":"GUEST_START","max_budget_vnd":5000}')
            session_id = station_io["res"][-1]["session"]["session_id"]
            await sessions.update_one(
                {"session_id": session_id},
                {"$set": {"started_at": datetime.now(UTC) - timedelta(seconds=65)}},
            )
            await handle_message(f"evcs/{station_id}/event", b'{"event":"UNPLUGGED"}')

            await req(station_id, '{"type":"STATION_SYNC"}')
            synced = station_io["res"][-1]
            assert synced["session"]["session_id"] == session_id
            assert synced["session"]["status"] == "WAITING_PAYMENT"
            assert synced["session"]["cost_vnd"] == 10
            assert synced["payment"]["status"] == "PENDING"
            assert synced["payment"]["amount_vnd"] == 10
        finally:
            await clean()

    run(scenario)
