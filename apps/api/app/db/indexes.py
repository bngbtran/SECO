from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

from app.db.mongodb import db


async def _ensure_index(collection, keys, **kwargs):
    try:
        await collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        # Existing indexes created manually may have an older definition.
        # The canonical setup is kept in scripts/mongodb_setup.js.
        if "already exists with a different name" in str(exc) or "IndexKeySpecsConflict" in str(exc):
            raise
        raise


async def create_indexes() -> None:
    await _ensure_index(
        db.users,
        [("user_id", ASCENDING)],
        unique=True,
        name="users_user_id_unique",
    )
    await _ensure_index(
        db.users,
        [("email", ASCENDING)],
        unique=True,
        name="users_email_unique",
    )

    await _ensure_index(
        db.rfid_cards,
        [("card_id", ASCENDING)],
        unique=True,
        name="rfid_card_id_unique",
    )

    await _ensure_index(
        db.rfid_cards,
        [("uid", ASCENDING)],
        unique=True,
        name="rfid_uid_unique",
        partialFilterExpression={"uid": {"$type": "string"}},
    )

    await _ensure_index(
        db.charging_stations,
        [("station_id", ASCENDING)],
        unique=True,
        name="stations_id_unique",
    )

    await _ensure_index(
        db.charging_sessions,
        [("session_id", ASCENDING)],
        unique=True,
        name="sessions_id_unique",
    )
    await _ensure_index(
        db.charging_sessions,
        [("station_id", ASCENDING), ("status", ASCENDING)],
        name="sessions_station_status",
    )
    await _ensure_index(
        db.charging_sessions,
        [("user_id", ASCENDING), ("started_at", DESCENDING)],
        name="sessions_user_started",
    )
    await _ensure_index(
        db.charging_sessions,
        [("station_id", ASCENDING)],
        unique=True,
        name="sessions_one_open_per_station",
        partialFilterExpression={"status": {"$in": ["ACTIVE", "WAITING_PAYMENT"]}},
    )

    await _ensure_index(
        db.charging_sessions,
        [("user_id", ASCENDING)],
        unique=True,
        name="sessions_one_open_per_user",
        partialFilterExpression={
            "status": {"$in": ["ACTIVE", "WAITING_PAYMENT"]},
            "user_id": {"$type": "string"},
        },
    )

    await _ensure_index(
        db.transactions,
        [("transaction_id", ASCENDING)],
        unique=True,
        name="transactions_id_unique",
    )
    await _ensure_index(
        db.transactions,
        [("session_id", ASCENDING)],
        name="transactions_session",
    )
    await _ensure_index(
        db.transactions,
        [("session_id", ASCENDING), ("type", ASCENDING)],
        unique=True,
        name="transactions_session_type_unique",
        partialFilterExpression={
            "session_id": {"$type": "string"},
        },
    )
    await _ensure_index(
        db.transactions,
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        name="transactions_user_created",
    )

    await _ensure_index(
        db.payment_intents,
        [("payment_id", ASCENDING)],
        unique=True,
        name="payments_id_unique",
    )
    await _ensure_index(
        db.payment_intents,
        [("session_id", ASCENDING)],
        name="payments_session",
    )
    await _ensure_index(
        db.payment_intents,
        [("session_id", ASCENDING)],
        unique=True,
        name="payments_one_pending_per_session",
        partialFilterExpression={"status": "PENDING"},
    )
    await _ensure_index(
        db.payment_intents,
        [("session_id", ASCENDING)],
        unique=True,
        name="payments_one_paid_per_session",
        partialFilterExpression={"status": "PAID"},
    )
    await _ensure_index(
        db.payment_intents,
        [("status", ASCENDING), ("expires_at", ASCENDING)],
        name="payments_status_expires",
    )

    await _ensure_index(
        db.wallet_holds,
        [("wallet_hold_id", ASCENDING)],
        unique=True,
        name="wallet_holds_id_unique",
    )
    await _ensure_index(
        db.wallet_holds,
        [("session_id", ASCENDING)],
        unique=True,
        name="wallet_holds_session_unique",
    )

    await _ensure_index(
        db.maintenance_records,
        [("maintenance_id", ASCENDING)],
        unique=True,
        name="maintenance_id_unique",
    )
    await _ensure_index(
        db.maintenance_records,
        [("station_id", ASCENDING), ("started_at", DESCENDING)],
        name="maintenance_station_started",
    )

    await _ensure_index(
        db.error_logs,
        [("error_id", ASCENDING)],
        unique=True,
        name="errors_id_unique",
    )
    await _ensure_index(
        db.error_logs,
        [("station_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="errors_station_occurred",
    )

    await _ensure_index(
        db.audit_logs,
        [("audit_id", ASCENDING)],
        unique=True,
        name="audit_id_unique",
    )
    await _ensure_index(
        db.audit_logs,
        [("created_at", DESCENDING)],
        name="audit_created",
    )
    await _ensure_index(
        db.audit_logs,
        [("actor_user_id", ASCENDING), ("created_at", DESCENDING)],
        name="audit_actor_created",
    )
