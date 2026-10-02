import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import SECOError
from app.db.mongodb import get_collection
from app.modules.admin.card_registration import handle_reader_message
from app.modules.charging.constants import (
    ConnectorLock,
    SessionStatus,
    SessionType,
    StationStatus,
    StopReason,
)
from app.modules.charging.realtime import to_jsonable
from app.modules.guest.realtime import (
    announce_guest_stopped,
    announce_payment_qr,
    broadcast_guest_session,
)
from app.modules.guest.service import (
    process_guest_session,
    request_new_payment,
    start_guest_charging,
    stop_guest_session,
)
from app.modules.logs.service import record_stop_error
from app.modules.member.realtime import announce_stopped, broadcast_session
from app.modules.member.service import (
    get_active_pending_budget,
    process_member_telemetry,
    start_member_charging,
    stop_member_session,
    verify_member_rfid,
)
from app.modules.station.device import (
    handle_station_sync,
    handle_technician_action,
    handle_technician_verify,
)
from app.mqtt import topics
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")

# Admin desk RFID reader (card registration), not a charging station.
_READER_TOPIC_RE = re.compile(r"^seco/reader/(?P<reader_id>[^/]+)/(?P<kind>scan|hb|status)$")

_TOPIC_RE = re.compile(
    r"^evcs/(?P<station_id>[^/]+)/(?P<kind>hb|telemetry|event|req|res|cmd|cmd_ack|status)$"
)

_EVENT_STOP_REASONS = {
    "UNPLUG": StopReason.UNPLUG.value,
    "UNPLUGGED": StopReason.UNPLUG.value,
    "ESTOP": StopReason.E_STOP.value,
    "E_STOP": StopReason.E_STOP.value,
    "OVER_TEMPERATURE": StopReason.OVER_TEMPERATURE.value,
    "OVERHEAT": StopReason.OVER_TEMPERATURE.value,
    "FAULT": StopReason.SYSTEM_ERROR.value,
    "SENSOR_FAULT": StopReason.SYSTEM_ERROR.value,
}

_SAFETY_STOP_REASONS = {
    StopReason.E_STOP.value,
    StopReason.OVER_TEMPERATURE.value,
    StopReason.SYSTEM_ERROR.value,
}

RequestHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def now_utc() -> datetime:
    return datetime.now(UTC)


async def publish(topic: str, payload: dict[str, Any]) -> None:
    # Imported lazily: app.mqtt.client imports this module.
    from app.mqtt.client import mqtt_service

    try:
        await mqtt_service.publish(topic, to_jsonable(payload))
    except RuntimeError:
        logger.warning("MQTT not connected; dropped publish to %s", topic)


def _session_brief(session: dict) -> dict[str, Any]:
    return {
        "session_id": session["session_id"],
        "status": session["status"],
        "max_budget_vnd": session.get("max_budget_vnd"),
        "energy_kwh": session.get("energy_kwh"),
        "cost_vnd": session.get("cost_vnd"),
        "stop_reason": session.get("stop_reason"),
    }


def _transaction_brief(transaction: dict | None) -> dict[str, Any] | None:
    if not transaction:
        return None
    return {
        "transaction_id": transaction["transaction_id"],
        "amount_vnd": transaction["amount_vnd"],
        "balance_after_vnd": transaction.get("balance_after_vnd"),
        "status": transaction["status"],
    }


class MQTTRequestDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, RequestHandler] = {}

    def register(self, request_type: str, handler: RequestHandler) -> None:
        self._handlers[request_type.upper()] = handler

    async def dispatch(
        self,
        station_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        request_type = str(payload.get("type", "")).upper()
        handler = self._handlers.get(request_type)

        if handler is None:
            logger.info("No handler for MQTT request type=%s station=%s", request_type, station_id)
            return None

        try:
            result = await handler(station_id, payload)
        except SECOError as exc:
            result = {"ok": False, "error": str(exc)}

        response = {"type": request_type, **result}
        if payload.get("request_id") is not None:
            response["request_id"] = payload["request_id"]
        response = to_jsonable(response)

        await publish(topics.response(station_id), response)
        return response


dispatcher = MQTTRequestDispatcher()


async def _handle_rfid_verify(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    card, user = await verify_member_rfid(uid=str(payload.get("uid", "")))
    pending = get_active_pending_budget(user, station_id)

    return {
        "ok": True,
        "card_id": card["card_id"],
        "user_id": user["user_id"],
        "name": user["name"],
        "role": user["role"],
        "balance_vnd": int(user.get("balance_vnd", 0)),
        "max_budget_vnd": pending["max_budget_vnd"] if pending else None,
    }


async def _handle_session_start(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    max_budget = payload.get("max_budget_vnd")

    result = await start_member_charging(
        uid=str(payload.get("uid", "")),
        station_id=station_id,
        max_budget_vnd=int(max_budget) if max_budget is not None else None,
    )
    session = result["session"]
    user = result["user"]

    await broadcast_session(session, "STARTED")

    return {
        "ok": True,
        "session": _session_brief(session),
        "user": {
            "user_id": user["user_id"],
            "name": user["name"],
            "balance_vnd": int(user.get("balance_vnd", 0)),
        },
    }


async def _handle_session_end(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("session_id", ""))

    session = await sessions.find_one(
        {"session_id": session_id, "station_id": station_id},
        {"_id": 0, "status": 1, "type": 1},
    )
    if not session:
        return {"ok": False, "error": "Charging session not found"}

    if session["type"] == SessionType.GUEST.value:
        result = await stop_guest_session(
            session_id=session_id,
            stop_reason=payload.get("reason") or StopReason.UNPLUG.value,
        )
        await announce_guest_stopped(result, send_stop=False)
        return {"ok": True, **_guest_stop_brief(result)}

    was_active = session["status"] == SessionStatus.ACTIVE.value
    result = await stop_member_session(
        session_id=session_id,
        stop_reason=payload.get("reason") or StopReason.UNPLUG.value,
    )
    if was_active:
        # The station ended the session itself, so no STOP command is needed.
        await announce_stopped(result, send_stop=False)

    return {
        "ok": True,
        "session": _session_brief(result["session"]),
        "transaction": _transaction_brief(result["transaction"]),
    }


def _payment_brief(payment: dict | None) -> dict[str, Any] | None:
    if not payment:
        return None
    return {
        "payment_id": payment["payment_id"],
        "amount_vnd": payment["amount_vnd"],
        "status": payment["status"],
        "qr_payload": payment["qr_payload"],
        "expires_at": payment["expires_at"],
    }


def _guest_stop_brief(result: dict) -> dict[str, Any]:
    return {
        "session": _session_brief(result["session"]),
        "requires_payment": result["requires_payment"],
        "payment": _payment_brief(result["payment"]),
    }


async def _handle_guest_start(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """UC-G02/G03: START pressed at the station, with an optional budget from the keypad."""
    max_budget = payload.get("max_budget_vnd")
    session = await start_guest_charging(
        station_id=station_id,
        max_budget_vnd=int(max_budget) if max_budget is not None else None,
    )
    await broadcast_guest_session(session, "STARTED")
    return {"ok": True, "session": _session_brief(session)}


async def _handle_guest_payment_request(
    station_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """UC-G10: the station asks for a new QR after the previous one expired."""
    payment = await request_new_payment(str(payload.get("session_id", "")))
    if payment["station_id"] != station_id:
        return {"ok": False, "error": "Session belongs to another station"}

    await announce_payment_qr(payment)
    return {"ok": True, "payment": _payment_brief(payment)}


dispatcher.register("RFID_VERIFY", _handle_rfid_verify)
dispatcher.register("SESSION_START", _handle_session_start)
dispatcher.register("SESSION_END", _handle_session_end)
dispatcher.register("GUEST_START", _handle_guest_start)
dispatcher.register("GUEST_PAYMENT_REQUEST", _handle_guest_payment_request)
dispatcher.register("TECHNICIAN_VERIFY", handle_technician_verify)
dispatcher.register("TECHNICIAN_ACTION", handle_technician_action)
dispatcher.register("STATION_SYNC", handle_station_sync)


def parse_topic(topic: str) -> tuple[str, str] | None:
    match = _TOPIC_RE.match(topic)
    if not match:
        return None
    return match.group("station_id"), match.group("kind")


def decode_payload(payload: bytes | str) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("MQTT payload must be a JSON object")
    return data


async def _mark_seen(station_id: str, extra: dict[str, Any] | None = None) -> None:
    now = now_utc()
    await stations.update_one(
        {"station_id": station_id},
        {
            "$set": {
                "is_online": True,
                "last_heartbeat_at": now,
                "updated_at": now,
                **(extra or {}),
            }
        },
    )


async def _handle_status(station_id: str, payload: bytes | str) -> dict[str, Any]:
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    raw = raw.strip()

    # Accept plain text (LWT) or a JSON object {"status": "ONLINE"}.
    try:
        parsed = json.loads(raw)
        station_status = str(parsed.get("status", "")) if isinstance(parsed, dict) else str(parsed)
    except json.JSONDecodeError:
        station_status = raw
    station_status = station_status.strip().upper()

    if station_status not in {"ONLINE", "OFFLINE"}:
        raise ValueError(f"Invalid station status payload: {raw}")

    is_online = station_status == "ONLINE"
    if is_online:
        await _mark_seen(station_id)
    else:
        await stations.update_one(
            {"station_id": station_id},
            {"$set": {"is_online": False, "updated_at": now_utc()}},
        )

    data = {"status": station_status, "is_online": is_online}
    await manager.broadcast({"type": "station_status", "station_id": station_id, "data": data})

    return {"type": "status", "station_id": station_id, "data": data}


async def _handle_telemetry(station_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0, "current_session_id": 1, "status": 1},
    )
    if not station:
        return None

    await _mark_seen(station_id, {"latest_telemetry": data})

    session_id = station.get("current_session_id")
    accounting = None

    if not session_id:
        # Idle station: sensor values only, for the admin/technician dashboard.
        await manager.broadcast(
            {"type": "station_telemetry", "station_id": station_id, "data": data}
        )

    open_session = (
        await sessions.find_one(
            {"session_id": session_id},
            {"_id": 0},
        )
        if session_id
        else None
    )

    if open_session and open_session["type"] == SessionType.GUEST.value:
        if open_session["status"] == SessionStatus.ACTIVE.value:
            accounting = await process_guest_session(open_session)
            if accounting["auto_stopped"]:
                await announce_guest_stopped(accounting)
            else:
                await broadcast_guest_session(
                    accounting["session"],
                    "TELEMETRY",
                    telemetry=data,
                )
        else:
            # Waiting for payment: the relay is off, report sensor values only.
            await manager.broadcast(
                {"type": "station_telemetry", "station_id": station_id, "data": data}
            )
    elif session_id and station.get("status") == StationStatus.CHARGING.value:
        accounting = await process_member_telemetry(station_id=station_id, telemetry=data)

        if accounting and accounting["auto_stopped"]:
            await announce_stopped(accounting)
        elif accounting:
            await broadcast_session(accounting["session"], "TELEMETRY", telemetry=data)
        else:
            # No session record for this station: relay the station's own values.
            await manager.broadcast(
                {
                    "type": "member_session_updated",
                    "event": "TELEMETRY",
                    "session_id": session_id,
                    "station_id": station_id,
                    "status": StationStatus.CHARGING.value,
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
        "accounting": to_jsonable(accounting) if accounting else None,
    }


async def _handle_event(station_id: str, data: dict[str, Any]) -> dict[str, Any]:
    event_type = str(data.get("event") or data.get("type") or "").upper()
    stop_reason = _EVENT_STOP_REASONS.get(event_type)
    result = None

    if stop_reason:
        station = await stations.find_one(
            {"station_id": station_id},
            {"_id": 0, "current_session_id": 1},
        )
        session_id = station.get("current_session_id") if station else None

        active = None
        if session_id:
            active = await sessions.find_one(
                {
                    "session_id": session_id,
                    "station_id": station_id,
                    "status": SessionStatus.ACTIVE.value,
                },
                {"_id": 0, "session_id": 1, "type": 1},
            )

        is_safety = stop_reason in _SAFETY_STOP_REASONS

        if active and active["type"] == SessionType.GUEST.value:
            result = await stop_guest_session(
                session_id=session_id,
                stop_reason=stop_reason,
                station_status=StationStatus.ERROR if is_safety else None,
            )
            await announce_guest_stopped(result)
        elif active:
            result = await stop_member_session(
                session_id=session_id,
                stop_reason=stop_reason,
                station_status=StationStatus.ERROR if is_safety else StationStatus.READY,
            )
            await announce_stopped(result)

        if is_safety:
            await record_stop_error(
                station_id=station_id,
                stop_reason=stop_reason,
                session_id=session_id if active else None,
                details=data,
            )
            if not active:
                # Idle station: still force the safe state (DD FR06).
                await stations.update_one(
                    {"station_id": station_id, "status": {"$ne": StationStatus.MAINTENANCE.value}},
                    {"$set": {"status": StationStatus.ERROR.value, "updated_at": now_utc()}},
                )
            await manager.broadcast(
                {
                    "type": "station_error",
                    "station_id": station_id,
                    "error_type": stop_reason,
                    "session_id": session_id if active else None,
                }
            )

    return {
        "type": "event",
        "station_id": station_id,
        "data": data,
        "result": to_jsonable(result) if result else None,
    }


async def _apply_lock_ack(station_id: str, data: dict[str, Any]) -> None:
    """UC-G06: the station reports whether the connector actually (un)locked."""
    command = str(data.get("command", "")).upper()
    if command not in {"LOCK", "UNLOCK"}:
        return

    if data.get("ok", True) is False:
        lock_state = ConnectorLock.UNKNOWN.value
    elif command == "LOCK":
        lock_state = ConnectorLock.LOCKED.value
    else:
        lock_state = ConnectorLock.UNLOCKED.value

    await stations.update_one(
        {"station_id": station_id},
        {"$set": {"connector_lock": lock_state, "updated_at": now_utc()}},
    )


async def handle_message(
    topic: str,
    payload: bytes | str,
) -> dict[str, Any] | None:
    reader = _READER_TOPIC_RE.match(topic)
    if reader:
        return await handle_reader_message(reader.group("reader_id"), reader.group("kind"), payload)

    parsed = parse_topic(topic)
    if parsed is None:
        logger.warning("Unknown MQTT topic: %s", topic)
        return None

    station_id, kind = parsed

    if kind == "status":
        return await _handle_status(station_id, payload)

    # Messages published by the backend itself come back through the wildcard subscription.
    if kind in {"res", "cmd"}:
        return None

    data = decode_payload(payload)

    if kind == "hb":
        await _mark_seen(station_id)
        return {"type": "heartbeat", "station_id": station_id, "data": data}

    if kind == "telemetry":
        return await _handle_telemetry(station_id, data)

    if kind == "event":
        return await _handle_event(station_id, data)

    if kind == "req":
        result = await dispatcher.dispatch(station_id, data)
        return {
            "type": "request",
            "station_id": station_id,
            "data": data,
            "result": result,
        }

    if kind == "cmd_ack":
        await _apply_lock_ack(station_id, data)
        await manager.broadcast(
            {"type": "station_command_ack", "station_id": station_id, "data": data}
        )
        return {"type": "cmd_ack", "station_id": station_id, "data": data}

    return None
