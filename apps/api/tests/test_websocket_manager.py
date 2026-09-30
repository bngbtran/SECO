from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_websocket_manager_broadcasts_to_connected_client():
    async def scenario():
        websocket = FakeWebSocket()

        manager.connections.add(websocket)

        try:
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

            await manager.broadcast(event)

            assert websocket.messages == [event]

        finally:
            manager.disconnect(websocket)

    TEST_LOOP.run_until_complete(scenario())