import asyncio
import logging
from typing import Any

from bson.decimal128 import Decimal128
from fastapi.encoders import jsonable_encoder

from app.core.config import get_settings
from app.db.mongodb import get_collection
from app.modules.charging.constants import StationStatus, StopReason
from app.modules.member.service import (
    list_active_member_sessions,
    process_member_session,
    session_live_fields,
    stop_member_session,
)
from app.mqtt import topics
from app.websocket.manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()

stations = get_collection("charging_stations")


def to_jsonable(value: Any) -> Any:
    return jsonable_encoder(
        value,
        custom_encoder={Decimal128: lambda v: float(v.to_decimal())},
    )


async def publish_station_command(station_id: str, payload: dict[str, Any]) -> None:
    # Imported lazily: app.mqtt.client imports the MQTT handlers, which import this module.
    from app.mqtt.client import mqtt_service

    try:
        await mqtt_service.publish(topics.command(station_id), to_jsonable(payload))
    except RuntimeError:
        logger.warning("MQTT not connected; command not sent station=%s", station_id)


def session_event(
    session: dict,
    event: str,
    *,
    telemetry: dict | None = None,
    transaction: dict | None = None,
) -> dict[str, Any]:
    """WebSocket payload for the member app (UC-U10 live view, UC-U12 result)."""
    payload = {
        "type": "member_session_updated",
        "event": event,
        "session_id": session["session_id"],
        "station_id": session["station_id"],
        "user_id": session.get("user_id"),
        "status": (
            StationStatus.CHARGING.value
            if session["status"] == "ACTIVE"
            else session["status"]
        ),
        "energy_kwh": session.get("energy_kwh"),
        "cost_vnd": session.get("cost_vnd"),
        "max_budget_vnd": session.get("max_budget_vnd"),
        "stop_reason": session.get("stop_reason"),
        **session_live_fields(session),
    }

    if telemetry is not None:
        payload.update(
            {
                "power_kw": telemetry.get("power_kw"),
                "voltage_v": telemetry.get("voltage_v"),
                "current_a": telemetry.get("current_a"),
                "temperature_c": telemetry.get("temperature_c"),
            }
        )

    if transaction is not None:
        payload.update(
            {
                "transaction_id": transaction["transaction_id"],
                "amount_vnd": transaction["amount_vnd"],
                "balance_after_vnd": transaction.get("balance_after_vnd"),
            }
        )

    return to_jsonable(payload)


async def broadcast_session(session: dict, event: str, **kwargs) -> None:
    await manager.broadcast(session_event(session, event, **kwargs))


async def announce_stopped(result: dict, *, send_stop: bool = True) -> None:
    """Tell the station to cut the relay and push the final result to the member app."""
    session = result["session"]

    if send_stop:
        await publish_station_command(
            session["station_id"],
            {
                "command": "STOP",
                "session_id": session["session_id"],
                "reason": session.get("stop_reason"),
            },
        )

    await broadcast_session(session, "STOPPED", transaction=result.get("transaction"))


async def tick_active_sessions() -> None:
    """Advance billing of every ACTIVE member session, independent of telemetry."""
    online = {
        station["station_id"]: station.get("is_online", False)
        async for station in stations.find({}, {"_id": 0, "station_id": 1, "is_online": 1})
    }

    for session in await list_active_member_sessions():
        try:
            if not online.get(session["station_id"], False):
                # Lost contact with the station: stop billing and flag it for a technician.
                result = await stop_member_session(
                    session_id=session["session_id"],
                    stop_reason=StopReason.SYSTEM_ERROR,
                    station_status=StationStatus.ERROR,
                )
                await announce_stopped(result)
                continue

            before = session.get("cost_vnd")
            result = await process_member_session(session)

            if result["auto_stopped"]:
                await announce_stopped(result)
            elif result["session"].get("cost_vnd") != before:
                await broadcast_session(result["session"], "TICK")
        except Exception:
            logger.exception("Session tick failed session=%s", session.get("session_id"))


async def run_session_ticker() -> None:
    while True:
        try:
            await tick_active_sessions()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Session ticker failed")

        await asyncio.sleep(settings.session_ticker_interval_seconds)
