from datetime import UTC, datetime
from uuid import uuid4

from app.db.mongodb import get_collection
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP

stations = get_collection("charging_stations")


def test_mqtt_telemetry_broadcasts_active_session(monkeypatch):
    async def scenario():
        station_id = f"TEST-{uuid4()}"
        session_id = str(uuid4())
        now = datetime.now(UTC)

        broadcasts = []

        async def fake_broadcast(data):
            broadcasts.append(data)

        monkeypatch.setattr(
            manager,
            "broadcast",
            fake_broadcast,
        )

        try:
            await stations.insert_one(
                {
                    "station_id": station_id,
                    "name": "MQTT Telemetry Test Station",
                    "status": "CHARGING",
                    "is_online": True,
                    "connector_lock": "UNLOCKED",
                    "current_session_id": session_id,
                    "last_heartbeat_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
            )

            telemetry = {
                "power_kw": 0.15,
                "energy_kwh": 0.0125,
                "cost_vnd": 50,
                "voltage_v": 12.1,
                "current_a": 1.2,
                "temperature_c": 32.5,
            }

            result = await handle_message(
                f"evcs/{station_id}/telemetry",
                (
                    b'{"power_kw": 0.15,'
                    b'"energy_kwh": 0.0125,'
                    b'"cost_vnd": 50,'
                    b'"voltage_v": 12.1,'
                    b'"current_a": 1.2,'
                    b'"temperature_c": 32.5}'
                ),
            )

            assert result["type"] == "telemetry"
            assert result["station_id"] == station_id
            assert result["session_id"] == session_id
            assert result["data"] == telemetry

            assert len(broadcasts) == 1

            event = broadcasts[0]

            assert event["type"] == "member_session_updated"
            assert event["event"] == "TELEMETRY"
            assert event["session_id"] == session_id
            assert event["station_id"] == station_id
            assert event["status"] == "CHARGING"
            assert event["energy_kwh"] == 0.0125
            assert event["power_kw"] == 0.15
            assert event["cost_vnd"] == 50
            assert event["voltage_v"] == 12.1
            assert event["current_a"] == 1.2
            assert event["temperature_c"] == 32.5

            stored_station = await stations.find_one(
                {"station_id": station_id},
                {
                    "_id": 0,
                    "is_online": 1,
                    "latest_telemetry": 1,
                    "current_session_id": 1,
                },
            )

            assert stored_station["is_online"] is True
            assert stored_station["current_session_id"] == session_id
            assert stored_station["latest_telemetry"] == telemetry

        finally:
            await stations.delete_one(
                {"station_id": station_id}
            )

    TEST_LOOP.run_until_complete(scenario())