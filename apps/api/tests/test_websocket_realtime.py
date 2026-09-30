from fastapi.testclient import TestClient

from app.main import app
from app.websocket.manager import manager


def test_websocket_receives_broadcast_event():
    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        event = {
            "type": "member_session_updated",
            "event": "TELEMETRY",
            "session_id": "SESSION-001",
            "station_id": "EVCS-01",
            "status": "CHARGING",
            "energy_kwh": 0.0125,
            "power_kw": 0.15,
            "cost_vnd": 50,
        }

        websocket.portal.call(manager.broadcast, event)

        received = websocket.receive_json()

        assert received == event

    assert len(manager.connections) == 0