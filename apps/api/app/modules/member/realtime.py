import logging
from typing import Any

from app.core.config import get_settings
from app.db.mongodb import get_collection
from app.modules.charging.constants import StationStatus, StopReason
from app.modules.charging.realtime import publish_station_command, to_jsonable
from app.modules.logs.service import record_error
from app.modules.member.service import (
    list_active_member_sessions,
    process_member_session,
    session_live_fields,
    stop_member_session,
)
from app.websocket.manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()

stations = get_collection("charging_stations")


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
        # The final bill, so the station shows what was actually charged.
        transaction = result.get("transaction") or {}
        await publish_station_command(
            session["station_id"],
            {
                "command": "STOP",
                "session_id": session["session_id"],
                "reason": session.get("stop_reason"),
                "type": session.get("type"),
                "energy_kwh": session.get("energy_kwh"),
                "cost_vnd": int(session.get("cost_vnd", 0)),
                "charged_vnd": transaction.get("amount_vnd", 0),
                "balance_after_vnd": transaction.get("balance_after_vnd"),
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
                await record_error(
                    station_id=session["station_id"],
                    error_type="COMMUNICATION_ERROR",
                    severity="ERROR",
                    session_id=session["session_id"],
                    message="Mất kết nối với trạm khi đang sạc, phiên bị dừng",
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
