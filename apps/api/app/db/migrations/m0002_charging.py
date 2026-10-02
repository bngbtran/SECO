"""DB-02: charging_sessions, wallet_holds, transactions (+ payment_intents for guests)."""

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.migrations.schema import sync_collection

VERSION = 2
NAME = "charging_sessions_wallet_transactions"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}
MONEY = {"bsonType": ["int", "long"], "minimum": 0}
NULLABLE_MONEY = {"bsonType": ["int", "long", "null"], "minimum": 0}

OPEN_SESSION = {"status": {"$in": ["ACTIVE", "WAITING_PAYMENT"]}}

CHARGING_SESSIONS = {
    "bsonType": "object",
    "required": [
        "session_id",
        "station_id",
        "type",
        "status",
        "energy_kwh",
        "power_kw",
        "price_per_kwh_vnd",
        "cost_vnd",
        "started_at",
        "ended_at",
        "connector_locked",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "session_id": STRING,
        "station_id": STRING,
        "type": {"enum": ["MEMBER", "GUEST"]},
        "user_id": NULLABLE_STRING,
        "vehicle_id": NULLABLE_STRING,
        "status": {"enum": ["ACTIVE", "WAITING_PAYMENT", "COMPLETED", "ERROR"]},
        "max_budget_vnd": NULLABLE_MONEY,
        # EXPLICIT: budget set by the driver; BALANCE: member capped at wallet; NONE: guest.
        "budget_mode": {"enum": ["EXPLICIT", "BALANCE", "NONE"]},
        "energy_kwh": {"bsonType": "decimal", "minimum": 0},
        "power_kw": {"bsonType": "decimal", "minimum": 0},
        # Snapshot of the tariff when the session started (DD 5).
        "price_per_kwh_vnd": MONEY,
        "cost_vnd": MONEY,
        "stop_reason": {
            "bsonType": ["string", "null"],
            "enum": [
                "UNPLUG",
                "MAX_BUDGET",
                "INSUFFICIENT_BALANCE",
                "TECHNICIAN",
                "OVER_TEMPERATURE",
                "E_STOP",
                "SYSTEM_ERROR",
                None,
            ],
        },
        "started_at": DATE,
        "ended_at": NULLABLE_DATE,
        "connector_locked": {"bsonType": "bool"},
        "created_at": DATE,
        "updated_at": DATE,
    },
}

WALLET_HOLDS = {
    "bsonType": "object",
    "required": [
        "wallet_hold_id",
        "user_id",
        "session_id",
        "amount_vnd",
        "status",
        "created_at",
    ],
    "properties": {
        "wallet_hold_id": STRING,
        "user_id": STRING,
        "session_id": STRING,
        "amount_vnd": MONEY,
        "status": {"enum": ["HOLDING", "RELEASED", "CANCELLED"]},
        "created_at": DATE,
        "released_at": NULLABLE_DATE,
    },
}

TRANSACTIONS = {
    "bsonType": "object",
    "required": ["transaction_id", "session_id", "type", "amount_vnd", "status", "created_at"],
    "properties": {
        "transaction_id": STRING,
        # null for TOPUP, which is not tied to a session.
        "session_id": NULLABLE_STRING,
        "user_id": NULLABLE_STRING,
        "payment_id": NULLABLE_STRING,
        "payment_method": NULLABLE_STRING,
        "type": {"enum": ["CHARGING", "GUEST_PAYMENT", "TOPUP"]},
        "amount_vnd": MONEY,
        "balance_before_vnd": NULLABLE_MONEY,
        "balance_after_vnd": NULLABLE_MONEY,
        "status": {"enum": ["COMPLETED", "FAILED"]},
        "created_at": DATE,
    },
}

PAYMENT_INTENTS = {
    "bsonType": "object",
    "required": [
        "payment_id",
        "session_id",
        "amount_vnd",
        "status",
        "provider",
        "qr_payload",
        "created_at",
        "expires_at",
        "paid_at",
        "updated_at",
    ],
    "properties": {
        "payment_id": STRING,
        "session_id": STRING,
        "station_id": STRING,
        "amount_vnd": MONEY,
        "status": {"enum": ["PENDING", "PAID", "EXPIRED", "CANCELLED"]},
        "provider": {"enum": ["SANDBOX"]},
        "qr_payload": STRING,
        "created_at": DATE,
        "expires_at": DATE,
        "paid_at": NULLABLE_DATE,
        "updated_at": DATE,
    },
}


async def up(db) -> None:
    await sync_collection(
        db,
        "charging_sessions",
        CHARGING_SESSIONS,
        [
            IndexModel([("session_id", ASCENDING)], unique=True, name="sessions_id_unique"),
            IndexModel(
                [("station_id", ASCENDING), ("status", ASCENDING)],
                name="sessions_station_status",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("started_at", DESCENDING)],
                name="sessions_user_started",
            ),
            # DD 6: at most one open session per station and per member.
            IndexModel(
                [("station_id", ASCENDING)],
                unique=True,
                name="sessions_one_open_per_station",
                partialFilterExpression=OPEN_SESSION,
            ),
            IndexModel(
                [("user_id", ASCENDING)],
                unique=True,
                name="sessions_one_open_per_user",
                partialFilterExpression={**OPEN_SESSION, "user_id": {"$type": "string"}},
            ),
        ],
    )

    await sync_collection(
        db,
        "wallet_holds",
        WALLET_HOLDS,
        [
            IndexModel(
                [("wallet_hold_id", ASCENDING)],
                unique=True,
                name="wallet_holds_id_unique",
            ),
            IndexModel(
                [("session_id", ASCENDING)],
                unique=True,
                name="wallet_holds_session_unique",
            ),
        ],
    )

    await sync_collection(
        db,
        "transactions",
        TRANSACTIONS,
        [
            IndexModel(
                [("transaction_id", ASCENDING)],
                unique=True,
                name="transactions_id_unique",
            ),
            IndexModel([("session_id", ASCENDING)], name="transactions_session"),
            # A session is charged / paid at most once per transaction type.
            IndexModel(
                [("session_id", ASCENDING), ("type", ASCENDING)],
                unique=True,
                name="transactions_session_type_unique",
                partialFilterExpression={"session_id": {"$type": "string"}},
            ),
            IndexModel(
                [("user_id", ASCENDING), ("created_at", DESCENDING)],
                name="transactions_user_created",
            ),
        ],
    )

    await sync_collection(
        db,
        "payment_intents",
        PAYMENT_INTENTS,
        [
            IndexModel([("payment_id", ASCENDING)], unique=True, name="payments_id_unique"),
            IndexModel([("session_id", ASCENDING)], name="payments_session"),
            IndexModel(
                [("status", ASCENDING), ("created_at", DESCENDING)],
                name="payments_status_created",
            ),
            IndexModel(
                [("status", ASCENDING), ("expires_at", ASCENDING)],
                name="payments_status_expires",
            ),
            # A guest session has at most one open QR and is paid at most once.
            IndexModel(
                [("session_id", ASCENDING)],
                unique=True,
                name="payments_one_pending_per_session",
                partialFilterExpression={"status": "PENDING"},
            ),
            IndexModel(
                [("session_id", ASCENDING)],
                unique=True,
                name="payments_one_paid_per_session",
                partialFilterExpression={"status": "PAID"},
            ),
        ],
    )
