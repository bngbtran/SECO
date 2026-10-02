from fastapi.testclient import TestClient

from app.main import app
from app.websocket.manager import manager


def test_websocket_endpoint_connects_and_disconnects():
    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        assert websocket is not None
        assert len(manager.connections) == 1

        websocket.send_text("ping")

        assert len(manager.connections) == 1

    assert len(manager.connections) == 0