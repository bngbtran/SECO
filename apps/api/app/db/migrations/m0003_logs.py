"""DB-03: maintenance_records, error_logs, audit_logs."""

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.migrations.schema import sync_collection

VERSION = 3
NAME = "maintenance_error_audit_logs"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}

MAINTENANCE_RECORDS = {
    "bsonType": "object",
    "required": [
        "maintenance_id",
        "station_id",
        "technician_id",
        "status",
        "started_at",
        "completed_at",
        "description",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "maintenance_id": STRING,
        "station_id": STRING,
        "technician_id": STRING,
        # The error that sent the station to MAINTENANCE, if any.
        "error_id": NULLABLE_STRING,
        "status": {"enum": ["IN_PROGRESS", "COMPLETED", "CANCELLED"]},
        "started_at": DATE,
        "completed_at": NULLABLE_DATE,
        "description": STRING,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

ERROR_LOGS = {
    "bsonType": "object",
    "required": [
        "error_id",
        "station_id",
        "error_type",
        "severity",
        "message",
        "occurred_at",
        "resolved_at",
        "resolved_by",
        "created_at",
    ],
    "properties": {
        "error_id": STRING,
        "station_id": STRING,
        "session_id": NULLABLE_STRING,
        "error_type": {
            "enum": [
                "OVER_TEMPERATURE",
                "E_STOP",
                "RELAY_FAULT",
                "COMMUNICATION_ERROR",
                "SENSOR_ERROR",
                "SYSTEM_ERROR",
            ]
        },
        "severity": {"enum": ["WARNING", "ERROR", "CRITICAL"]},
        "message": STRING,
        "details": {"bsonType": ["object", "null"]},
        "occurred_at": DATE,
        "resolved_at": NULLABLE_DATE,
        "resolved_by": NULLABLE_STRING,
        "created_at": DATE,
    },
}

AUDIT_LOGS = {
    "bsonType": "object",
    "required": [
        "audit_id",
        "actor_user_id",
        "actor_role",
        "action",
        "target_type",
        "target_id",
        "created_at",
    ],
    "properties": {
        "audit_id": STRING,
        "actor_user_id": STRING,
        "actor_role": {"enum": ["USER", "ADMIN", "TECHNICIAN"]},
        "action": STRING,
        "target_type": STRING,
        "target_id": STRING,
        "metadata": {"bsonType": "object"},
        "created_at": DATE,
    },
}


async def up(db) -> None:
    await sync_collection(
        db,
        "maintenance_records",
        MAINTENANCE_RECORDS,
        [
            IndexModel(
                [("maintenance_id", ASCENDING)],
                unique=True,
                name="maintenance_id_unique",
            ),
            IndexModel(
                [("station_id", ASCENDING), ("started_at", DESCENDING)],
                name="maintenance_station_started",
            ),
            IndexModel(
                [("technician_id", ASCENDING), ("started_at", DESCENDING)],
                name="maintenance_technician_started",
            ),
        ],
    )

    await sync_collection(
        db,
        "error_logs",
        ERROR_LOGS,
        [
            IndexModel([("error_id", ASCENDING)], unique=True, name="errors_id_unique"),
            IndexModel(
                [("station_id", ASCENDING), ("occurred_at", DESCENDING)],
                name="errors_station_occurred",
            ),
            # Open errors per station, for the technician dashboard.
            IndexModel(
                [("station_id", ASCENDING), ("resolved_at", ASCENDING)],
                name="errors_station_resolved",
            ),
        ],
    )

    await sync_collection(
        db,
        "audit_logs",
        AUDIT_LOGS,
        [
            IndexModel([("audit_id", ASCENDING)], unique=True, name="audit_id_unique"),
            IndexModel([("created_at", DESCENDING)], name="audit_created"),
            IndexModel(
                [("actor_user_id", ASCENDING), ("created_at", DESCENDING)],
                name="audit_actor_created",
            ),
            IndexModel(
                [("target_type", ASCENDING), ("target_id", ASCENDING)],
                name="audit_target",
            ),
        ],
    )
