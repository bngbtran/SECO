import logging
from typing import Any

from app.db.mongodb import get_collection
from app.modules.charging.constants import StationStatus, StopReason
from app.modules.charging.realtime import publish_station_command, to_jsonable
from app.modules.charging.usage import session_live_fields
from app.modules.guest.service import (
    expire_overdue_payments,
    list_active_guest_sessions,
    process_guest_session,
    stop_guest_session,
)
from app.modules.logs.service import record_error
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

stations = get_collection("charging_stations")


def _payment_brief(payment: dict | None) -> dict | None:
    if not payment:
        return None
    return {
        "payment_id": payment["payment_id"],
        "amount_vnd": payment["amount_vnd"],
        "status": payment["status"],
        "qr_payload": payment["qr_payload"],
        "expires_at": payment["expires_at"],
    }


def guest_session_event(
    session: dict,
    event: str,
    *,
    telemetry: dict | None = None,
    payment: dict | None = None,
) -> dict[str, Any]:
    """WebSocket payload for the station screen / guest web page.

    Guests have no account, so these events carry no user_id and reach every
    client; they contain no personal data.
    """
    payload = {
        "type": "guest_session_updated",
        "event": event,
        "session_id": session["session_id"],
        "station_id": session["station_id"],
        "status": session["status"],
        "energy_kwh": session.get("energy_kwh"),
        "cost_vnd": session.get("cost_vnd"),
        "max_budget_vnd": session.get("max_budget_vnd"),
        "stop_reason": session.get("stop_reason"),
        "connector_locked": session.get("connector_locked"),
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

    if payment is not None:
        payload["payment"] = _payment_brief(payment)

    return to_jsonable(payload)


async def broadcast_guest_session(session: dict, event: str, **kwargs) -> None:
    await manager.broadcast(guest_session_event(session, event, **kwargs))


async def announce_payment_qr(payment: dict) -> None:
    """Show the QR on the station LCD and the guest web page."""
    await publish_station_command(
        payment["station_id"],
        {
            "command": "PAYMENT_QR",
            "session_id": payment["session_id"],
            **_payment_brief(payment),
        },
    )
    await manager.broadcast(
        to_jsonable(
            {
                "type": "guest_payment_updated",
                "station_id": payment["station_id"],
                "session_id": payment["session_id"],
                **_payment_brief(payment),
            }
        )
    )


async def announce_payment_status(payment: dict) -> None:
    """QR expired or cancelled: the connector stays locked until a new QR is paid."""
    await publish_station_command(
        payment["station_id"],
        {
            "command": "PAYMENT_STATUS",
            "session_id": payment["session_id"],
            "payment_id": payment["payment_id"],
            "status": payment["status"],
        },
    )
    await manager.broadcast(
        to_jsonable(
            {
                "type": "guest_payment_updated",
                "station_id": payment["station_id"],
                "session_id": payment["session_id"],
                **_payment_brief(payment),
            }
        )
    )


async def announce_guest_stopped(result: dict, *, send_stop: bool = True) -> None:
    """UC-G05/G06/G07: cut the relay, lock the connector and show the QR."""
    if not result.get("stopped_now", True):
        return

    session = result["session"]
    station_id = session["station_id"]

    if send_stop:
        await publish_station_command(
            station_id,
            {
                "command": "STOP",
                "session_id": session["session_id"],
                "reason": session.get("stop_reason"),
                "type": session.get("type"),
                "energy_kwh": session.get("energy_kwh"),
                "cost_vnd": int(session.get("cost_vnd", 0)),
                # False: cost 0 (under one 30 s tick), COMPLETED without a QR.
                "requires_payment": result["requires_payment"],
            },
        )

    if not result["requires_payment"]:
        await broadcast_guest_session(session, "COMPLETED")
        return

    await publish_station_command(
        station_id,
        {"command": "LOCK", "session_id": session["session_id"]},
    )
    await announce_payment_qr(result["payment"])
    await broadcast_guest_session(session, "WAITING_PAYMENT", payment=result["payment"])


async def announce_paid(result: dict) -> None:
    """UC-G09: payment confirmed, release the connector."""
    if not result.get("paid_now", True):
        return

    session = result["session"]
    await publish_station_command(
        session["station_id"],
        {"command": "UNLOCK", "session_id": session["session_id"]},
    )
    await broadcast_guest_session(session, "PAID", payment=result["payment"])


async def tick_guest_sessions() -> None:
    """Advance billing of every ACTIVE guest session, independent of telemetry."""
    online = {
        station["station_id"]: station.get("is_online", False)
        async for station in stations.find({}, {"_id": 0, "station_id": 1, "is_online": 1})
    }

    for session in await list_active_guest_sessions():
        try:
            if not online.get(session["station_id"], False):
                result = await stop_guest_session(
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
                await announce_guest_stopped(result)
                continue

            before = session.get("cost_vnd")
            result = await process_guest_session(session)

            if result["auto_stopped"]:
                await announce_guest_stopped(result)
            elif result["session"].get("cost_vnd") != before:
                await broadcast_guest_session(result["session"], "TICK")
        except Exception:
            logger.exception("Guest session tick failed session=%s", session.get("session_id"))


async def tick_payment_expiry() -> None:
    for payment in await expire_overdue_payments():
        await announce_payment_status(payment)
