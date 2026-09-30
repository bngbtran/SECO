"""FR02: assign / replace a member's RFID card by scanning it on the admin desk reader."""

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.migrations.schema import sync_collection

VERSION = 5
NAME = "card_registrations_rfid_readers"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}

OPEN = ["WAITING", "SCANNED"]

CARD_REGISTRATIONS = {
    "bsonType": "object",
    "required": [
        "registration_id",
        "reader_id",
        "user_id",
        "mode",
        "status",
        "uid",
        "created_by",
        "created_at",
        "expires_at",
        "updated_at",
    ],
    "properties": {
        "registration_id": STRING,
        # Admin desk RFID reader that scans the card (not a charging station).
        "reader_id": STRING,
        # The member the card will belong to.
        "user_id": STRING,
        # ASSIGN: add a card; REPLACE: the new card replaces replace_card_id.
        "mode": {"enum": ["ASSIGN", "REPLACE"]},
        "replace_card_id": NULLABLE_STRING,
        # WAITING: reader waits for a card; SCANNED: UID received, waits for the
        # admin to confirm; then COMPLETED, CANCELLED or EXPIRED.
        "status": {"enum": ["WAITING", "SCANNED", "COMPLETED", "CANCELLED", "EXPIRED"]},
        "uid": NULLABLE_STRING,
        # Why the last scan was rejected (e.g. the card belongs to someone else).
        "last_error": NULLABLE_STRING,
        "card_id": NULLABLE_STRING,
        "created_by": STRING,
        "created_at": DATE,
        "expires_at": DATE,
        "scanned_at": NULLABLE_DATE,
        "completed_at": NULLABLE_DATE,
        "updated_at": DATE,
    },
}

RFID_READERS = {
    "bsonType": "object",
    "required": ["reader_id", "is_online", "updated_at"],
    "properties": {
        "reader_id": STRING,
        "is_online": {"bsonType": "bool"},
        "firmware": NULLABLE_STRING,
        "last_seen_at": NULLABLE_DATE,
        "updated_at": DATE,
    },
}


async def up(db) -> None:
    await sync_collection(
        db,
        "card_registrations",
        CARD_REGISTRATIONS,
        [
            IndexModel(
                [("registration_id", ASCENDING)],
                unique=True,
                name="card_registrations_id_unique",
            ),
            # A reader scans for one registration at a time.
            IndexModel(
                [("reader_id", ASCENDING)],
                unique=True,
                name="card_registrations_one_open_per_reader",
                partialFilterExpression={"status": {"$in": OPEN}},
            ),
            IndexModel([("created_at", DESCENDING)], name="card_registrations_created"),
        ],
    )

    await sync_collection(
        db,
        "rfid_readers",
        RFID_READERS,
        [IndexModel([("reader_id", ASCENDING)], unique=True, name="rfid_readers_id_unique")],
    )
