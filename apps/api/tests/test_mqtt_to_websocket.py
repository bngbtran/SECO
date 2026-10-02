import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.mongodb import get_collection
from app.main import app
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP


def test_mqtt_telemetry_reaches_websocket_client(monkeypatch):
    station_id = f"EVCS-TEST-{uuid4().hex[:8]}"
    session_id = f"SESSION-{uuid4().hex[:8]}"

    stations = get_collection("charging_stations")

    station = {
        "station_id": station_id,
        "name": "SECO Test Station",
        "status": "CHARGING",
        "is_online": True,
        "connector_lock": "LOCKED",
        "current_session_id": session_id,
        "last_heartbeat_at": datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    telemetry = {
        "energy_kwh": 0.025,
        "power_kw": 0.15,
        "cost_vnd": 100,
        "voltage_v": 220.0,
        "current_a": 0.68,
        "temperature_c": 32.0,
    }

    async def send_from_server(websocket, data):
        await websocket.send_json(data)

    async def relay_broadcast(data):
        server_websocket = next(iter(manager.connections))
        websocket.portal.call(
            send_from_server,
            server_websocket,
            data,
        )

    monkeypatch.setattr(manager, "broadcast", relay_broadcast)

    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        TEST_LOOP.run_until_complete(stations.insert_one(station))

        try:
            result = TEST_LOOP.run_until_complete(
                handle_message(
                    f"evcs/{station_id}/telemetry",
                    json.dumps(telemetry).encode(),
                )
            )

            assert result["type"] == "telemetry"
            assert result["station_id"] == station_id
            assert result["session_id"] == session_id
            assert result["data"] == telemetry

            received = websocket.receive_json()

            assert received == {
                "type": "member_session_updated",
                "event": "TELEMETRY",
                "session_id": session_id,
                "station_id": station_id,
                "status": "CHARGING",
                "energy_kwh": 0.025,
                "power_kw": 0.15,
                "cost_vnd": 100,
                "voltage_v": 220.0,
                "current_a": 0.68,
                "temperature_c": 32.0,
            }

        finally:
            TEST_LOOP.run_until_complete(
                stations.delete_one({"station_id": station_id})
            )