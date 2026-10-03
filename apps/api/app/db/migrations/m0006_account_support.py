"""Account self-service, notifications, support tickets, favourite stations.

- password_resets: temporary passwords for "forgot password" (members, technicians).
- notifications: in-app inbox of every user (member app, technician app).
- support_tickets: a member asks for help at a station; technicians handle it.
- favorite_stations: stations a member pinned.
- rfid_cards.block_reason: the owner locked the card (LOCKED, reversible) or
  reported it lost (LOST, only an admin replaces it).
"""

import copy

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.migrations import m0004_admin
from app.db.migrations.schema import sync_collection, sync_validator

VERSION = 6
NAME = "account_notifications_support_favorites"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}
COUNT = {"bsonType": ["int", "long"], "minimum": 0}

ROLES = ["USER", "ADMIN", "TECHNICIAN"]
TICKET_OPEN = ["OPEN", "IN_PROGRESS"]

RFID_CARDS = copy.deepcopy(m0004_admin.RFID_CARDS)
RFID_CARDS["properties"].update(
    {
        "block_reason": {"enum": ["LOCKED", "LOST", None]},
        "blocked_at": NULLABLE_DATE,
        "blocked_by": NULLABLE_STRING,
    }
)

PASSWORD_RESETS = {
    "bsonType": "object",
    "required": [
        "reset_id",
        "user_id",
        "temp_password_hash",
        "attempts",
        "expires_at",
        "used_at",
        "created_at",
    ],
    "properties": {
        "reset_id": STRING,
        "user_id": STRING,
        # Argon2 hash of the temporary password; the password itself is never stored.
        "temp_password_hash": STRING,
        "attempts": COUNT,
        "expires_at": DATE,
        "used_at": NULLABLE_DATE,
        "created_at": DATE,
    },
}

NOTIFICATIONS = {
    "bsonType": "object",
    "required": [
        "notification_id",
        "user_id",
        "role",
        "type",
        "title",
        "body",
        "data",
        "read_at",
        "created_at",
    ],
    "properties": {
        "notification_id": STRING,
        "user_id": STRING,
        "role": {"enum": ROLES},
        "type": STRING,
        "title": {"bsonType": "string", "minLength": 1},
        "body": STRING,
        # Ids the app needs to open the related screen (session_id, ticket_id, ...).
        "data": {"bsonType": "object"},
        "read_at": NULLABLE_DATE,
        "created_at": DATE,
    },
}

SUPPORT_TICKETS = {
    "bsonType": "object",
    "required": [
        "ticket_id",
        "user_id",
        "station_id",
        "category",
        "description",
        "status",
        "station_snapshot",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "ticket_id": STRING,
        "user_id": STRING,
        "station_id": STRING,
        "category": {
            "enum": [
                "CANNOT_START",
                "CHARGING_INTERRUPTED",
                "CONNECTOR_STUCK",
                "PAYMENT",
                "CARD",
                "STATION_DAMAGED",
                "OTHER",
            ]
        },
        "description": {"bsonType": "string", "maxLength": 1000},
        # OPEN -> IN_PROGRESS (a technician took it) -> RESOLVED; the member can
        # cancel while it is OPEN.
        "status": {"enum": ["OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED"]},
        # Station state when the member asked for help (status, telemetry, open errors).
        "station_snapshot": {"bsonType": "object"},
        # The member's session at that station, if any (active or most recent).
        "session_id": NULLABLE_STRING,
        "assigned_to": NULLABLE_STRING,
        "assigned_at": NULLABLE_DATE,
        "resolution_note": NULLABLE_STRING,
        "resolved_at": NULLABLE_DATE,
        "cancelled_at": NULLABLE_DATE,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

FAVORITE_STATIONS = {
    "bsonType": "object",
    "required": ["user_id", "station_id", "created_at"],
    "properties": {
        "user_id": STRING,
        "station_id": STRING,
        "created_at": DATE,
    },
}


async def up(db) -> None:
    await sync_validator(db, "rfid_cards", RFID_CARDS)

    await sync_collection(
        db,
        "password_resets",
        PASSWORD_RESETS,
        [
            IndexModel([("reset_id", ASCENDING)], unique=True, name="password_resets_id_unique"),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="password_resets_user"),
            # Mongo removes them an hour after they expire.
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=3600, name="password_resets_ttl"),
        ],
    )

    await sync_collection(
        db,
        "notifications",
        NOTIFICATIONS,
        [
            IndexModel(
                [("notification_id", ASCENDING)],
                unique=True,
                name="notifications_id_unique",
            ),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="notifications_inbox"),
            IndexModel(
                [("user_id", ASCENDING), ("read_at", ASCENDING)],
                name="notifications_unread",
            ),
            # Keep 90 days of history.
            IndexModel(
                [("created_at", ASCENDING)],
                expireAfterSeconds=90 * 24 * 3600,
                name="notifications_ttl",
            ),
        ],
    )

    await sync_collection(
        db,
        "support_tickets",
        SUPPORT_TICKETS,
        [
            IndexModel([("ticket_id", ASCENDING)], unique=True, name="support_tickets_id_unique"),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="support_tickets_user"),
            IndexModel([("status", ASCENDING), ("created_at", DESCENDING)], name="support_tickets_queue"),
            IndexModel(
                [("assigned_to", ASCENDING), ("created_at", DESCENDING)],
                name="support_tickets_assignee",
            ),
            # One open request per member and station: no duplicate alerts.
            IndexModel(
                [("user_id", ASCENDING), ("station_id", ASCENDING)],
                unique=True,
                name="support_tickets_one_open_per_station",
                partialFilterExpression={"status": {"$in": TICKET_OPEN}},
            ),
        ],
    )

    await sync_collection(
        db,
        "favorite_stations",
        FAVORITE_STATIONS,
        [
            IndexModel(
                [("user_id", ASCENDING), ("station_id", ASCENDING)],
                unique=True,
                name="favorite_stations_unique",
            ),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="favorite_stations_user"),
        ],
    )
