"""Audit log (who did what, UC-A13) and station error log (UC-A12)."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db.mongodb import get_collection

logger = logging.getLogger(__name__)

audit_logs = get_collection("audit_logs")
error_logs = get_collection("error_logs")

# MQTT stop reason -> (error_type, severity) in error_logs.
_ERROR_FOR_REASON = {
    "E_STOP": ("E_STOP", "CRITICAL"),
    "OVER_TEMPERATURE": ("OVER_TEMPERATURE", "CRITICAL"),
    "SYSTEM_ERROR": ("SYSTEM_ERROR", "ERROR"),
}

_ERROR_MESSAGES = {
    "E_STOP": "Nút E-Stop được nhấn, relay bị cắt",
    "OVER_TEMPERATURE": "Nhiệt độ vượt ngưỡng, relay bị cắt",
    "SYSTEM_ERROR": "Trạm báo lỗi hệ thống",
    "SENSOR_ERROR": "Lỗi cảm biến",
    "COMMUNICATION_ERROR": "Mất kết nối với trạm",
}


def now_utc() -> datetime:
    return datetime.now(UTC)


async def record_audit(
    *,
    actor: dict,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best effort: an audit failure must not undo the action it describes."""
    try:
        await audit_logs.insert_one(
            {
                "audit_id": str(uuid4()),
                "actor_user_id": actor["user_id"],
                "actor_name": actor.get("name"),
                "actor_role": actor["role"],
                "action": action,
                "target_type": target_type,
                "target_id": str(target_id),
                "metadata": metadata or {},
                "created_at": now_utc(),
            }
        )
    except Exception:
        logger.exception("Failed to write audit log action=%s target=%s", action, target_id)


async def record_error(
    *,
    station_id: str,
    error_type: str,
    severity: str,
    message: str | None = None,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict | None:
    now = now_utc()
    document = {
        "error_id": str(uuid4()),
        "station_id": station_id,
        "session_id": session_id,
        "error_type": error_type,
        "severity": severity,
        "message": message or _ERROR_MESSAGES.get(error_type, error_type),
        "details": details,
        "occurred_at": now,
        "resolved_at": None,
        "resolved_by": None,
        "created_at": now,
    }
    try:
        await error_logs.insert_one(document)
    except Exception:
        logger.exception("Failed to write error log station=%s type=%s", station_id, error_type)
        return None

    document.pop("_id", None)
    return document


async def record_stop_error(
    *,
    station_id: str,
    stop_reason: str,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict | None:
    """Log a safety stop (E-Stop, over-temperature, fault) reported by a station."""
    error_type, severity = _ERROR_FOR_REASON.get(stop_reason, ("SYSTEM_ERROR", "ERROR"))
    return await record_error(
        station_id=station_id,
        error_type=error_type,
        severity=severity,
        session_id=session_id,
        details=details,
    )
