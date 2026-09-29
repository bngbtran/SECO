import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.db.mongodb import get_collection
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

_TOPIC_RE = re.compile(r"^evcs/(?P<station_id>[^/]+)/(?P<kind>[^/]+)$")


RequestHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any] | None]]


class MQTTRequestDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, RequestHandler] = {}

    def register(self, request_type: str, handler: RequestHandler) -> None:
        self._handlers[request_type] = handler

    async def dispatch(self, station_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        request_type = payload.get("type")
        if not request_type:
            return None
        handler = self._handlers.get(request_type)
        if not handler:
            logger.info("No handler for MQTT request type=%s station=%s", request_type, station_id)
            return None
        return await handler(station_id, payload)


stations = get_collection("charging_stations")
dispatcher = MQTTRequestDispatcher()


def parse_topic(topic: str) -> tuple[str, str] | None:
    match = _TOPIC_RE.match(topic)
    if not match:
        return None
    return match.group("station_id"), match.group("kind")


def decode_payload(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("MQTT payload must be a JSON object")
    return value


async def handle_message(
    topic: str,
    payload: bytes,
) -> dict[str, Any] | None:
    parsed = parse_topic(topic)
    if not parsed:
        return None

    station_id, kind = parsed
    now = datetime.now(UTC)

    # /status uses plain text: ONLINE / OFFLINE
    if kind == "status":
        status = payload.decode("utf-8").strip().upper()

        if status not in {"ONLINE", "OFFLINE"}:
            raise ValueError(
                f"Invalid station status payload: {status}"
            )

        is_online = status == "ONLINE"

        await stations.update_one(
            {"station_id": station_id},
            {
                "$set": {
                    "is_online": is_online,
                    "updated_at": now,
                }
            },
        )

        return {
            "type": "status",
            "station_id": station_id,
            "data": {
                "status": status,
                "is_online": is_online,
            },
        }

    # All other MQTT payloads are JSON objects
    data = decode_payload(payload)

    if kind == "hb":
        await stations.update_one(
            {"station_id": station_id},
            {
                "$set": {
                    "is_online": True,
                    "last_heartbeat_at": now,
                    "updated_at": now,
                }
            },
        )
        return {
            "type": "heartbeat",
            "station_id": station_id,
            "data": data,
        }

    if kind == "telemetry":
        await stations.update_one(
            {"station_id": station_id},
            {
                "$set": {
                    "is_online": True,
                    "latest_telemetry": data,
                    "last_telemetry_at": now,
                    "updated_at": now,
                }
            },
        )

        station = await stations.find_one(
            {"station_id": station_id},
            {
                "_id": 0,
                "current_session_id": 1,
                "status": 1,
            },
        )

        session_id = (
            station.get("current_session_id")
            if station
            else None
        )

        if session_id and station.get("status") == "CHARGING":
            await manager.broadcast(
                {
                    "type": "member_session_updated",
                    "event": "TELEMETRY",
                    "session_id": session_id,
                    "station_id": station_id,
                    "status": "CHARGING",
                    "energy_kwh": data.get("energy_kwh"),
                    "power_kw": data.get("power_kw"),
                    "cost_vnd": data.get("cost_vnd"),
                    "voltage_v": data.get("voltage_v"),
                    "current_a": data.get("current_a"),
                    "temperature_c": data.get("temperature_c"),
                }
            )

        return {
            "type": "telemetry",
            "station_id": station_id,
            "session_id": session_id,
            "data": data,
        }

    if kind == "event":
        return {
            "type": "event",
            "station_id": station_id,
            "data": data,
        }

    if kind == "req":
        result = await dispatcher.dispatch(
            station_id,
            data,
        )
        return {
            "type": "request",
            "station_id": station_id,
            "data": data,
            "result": result,
        }

    if kind == "cmd_ack":
        return {
            "type": "cmd_ack",
            "station_id": station_id,
            "data": data,
        }

    if kind == "res":
        return {
            "type": "response",
            "station_id": station_id,
            "data": data,
        }

    return None
