"""Station requests that are not member / guest charging (simulator/station).

TECHNICIAN_VERIFY  {uid}            -> is this card a technician's card?
TECHNICIAN_ACTION  {uid, action}    -> ENTER_MAINTENANCE | EXIT_MAINTENANCE (DD 3.3:
                                       RFID technician + STOP held 3 s, both ways)
STATION_SYNC       {}               -> station state after a (re)boot, so the
                                       firmware resumes a session, a pending QR
                                       or the ERROR / MAINTENANCE state (with the
                                       latest open error, to show why).
"""

from typing import Any

from app.core.exceptions import BusinessRuleError, SECOError
from app.db.mongodb import get_collection
from app.modules.charging.constants import SessionStatus, StationStatus
from app.modules.charging.usage import serialize_session, session_live_fields

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
payment_intents = get_collection("payment_intents")
rfid_cards = get_collection("rfid_cards")
error_logs = get_collection("error_logs")
users = get_collection("users")

ACTIONS = {
    "ENTER_MAINTENANCE": "MAINTENANCE",
    "EXIT_MAINTENANCE": "READY",
}


async def _technician(uid: str) -> dict:
    card = await rfid_cards.find_one(
        {"uid": uid.strip().upper(), "is_active": True, "pending": False},
        {"_id": 0, "user_id": 1},
    )
    user = (
        await users.find_one({"user_id": card["user_id"]}, {"_id": 0, "password_hash": 0})
        if card and card.get("user_id")
        else None
    )
    if not user or not user.get("is_active") or user.get("role") != "TECHNICIAN":
        raise BusinessRuleError("NOT_TECHNICIAN")
    return user


async def handle_technician_verify(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user = await _technician(str(payload.get("uid", "")))
    station = await stations.find_one({"station_id": station_id}, {"_id": 0, "status": 1})
    return {
        "ok": True,
        "user_id": user["user_id"],
        "name": user["name"],
        "station_status": station["status"] if station else None,
    }


async def handle_technician_action(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily: the admin module imports the MQTT client, which imports the handlers.
    from app.modules.admin.stations import send_command

    action = str(payload.get("action", "")).upper()
    if action not in ACTIONS:
        return {"ok": False, "error": "UNKNOWN_ACTION"}

    user = await _technician(str(payload.get("uid", "")))
    station = await stations.find_one({"station_id": station_id}, {"_id": 0, "status": 1})
    if not station:
        return {"ok": False, "error": "Station not found"}

    if action == "ENTER_MAINTENANCE" and station["status"] not in (
        StationStatus.ERROR.value,
        StationStatus.READY.value,
    ):
        return {"ok": False, "error": f"STATION_{station['status']}"}
    if action == "EXIT_MAINTENANCE" and station["status"] != StationStatus.MAINTENANCE.value:
        return {"ok": False, "error": f"STATION_{station['status']}"}

    try:
        result = await send_command(
            station_id,
            ACTIONS[action],
            f"Kỹ thuật viên {user['name']} (RFID + STOP 3s)",
            user,
        )
    except SECOError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "action": action,
        "name": user["name"],
        "station_status": result["station"]["status"],
    }


async def handle_station_sync(station_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    station = await stations.find_one({"station_id": station_id}, {"_id": 0})
    if not station:
        return {"ok": False, "error": "Station not found"}

    session = None
    if station.get("current_session_id"):
        session = await sessions.find_one({"session_id": station["current_session_id"]}, {"_id": 0})

    payment = None
    if session and session["status"] == SessionStatus.WAITING_PAYMENT.value:
        payment = await payment_intents.find_one(
            {"session_id": session["session_id"]},
            {"_id": 0, "payment_id": 1, "amount_vnd": 1, "status": 1, "qr_payload": 1, "expires_at": 1},
            sort=[("created_at", -1)],
        )

    error = None
    if station["status"] == StationStatus.ERROR.value:
        # The safety stop that put the station in ERROR (not e.g. a
        # COMMUNICATION_ERROR logged while it was offline), with its details
        # (event, temperature_c / current_a) so the screen can show why.
        error = await error_logs.find_one(
            {
                "station_id": station_id,
                "resolved_at": None,
                "error_type": {"$in": ["E_STOP", "OVER_TEMPERATURE", "SYSTEM_ERROR"]},
            },
            {"_id": 0, "error_type": 1, "message": 1, "details": 1, "occurred_at": 1},
            sort=[("occurred_at", -1)],
        )

    return {
        "ok": True,
        "status": station["status"],
        "error": error,
        "connector_lock": station.get("connector_lock"),
        "session": (
            {
                **{
                    k: v
                    for k, v in serialize_session(session).items()
                    if k
                    in (
                        "session_id",
                        "type",
                        "status",
                        "max_budget_vnd",
                        "energy_kwh",
                        "cost_vnd",
                        "price_per_kwh_vnd",
                        "stop_reason",
                    )
                },
                **session_live_fields(session),
                **_measured_energy(session),
            }
            if session
            else None
        ),
        "payment": payment,
    }


def _measured_energy(session: dict) -> dict[str, Any]:
    """Energy the station measured and the API recorded: it resumes counting from it."""
    if not session.get("metered"):
        return {}
    return {
        "energy_kwh_sim": session.get("metered_energy_kwh"),
        "energy_kwh_raw": session.get("metered_energy_kwh_raw"),
    }
