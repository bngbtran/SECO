"""Admin: price history (UC-A04), unlinked RFID cards (UC-A02), user phone."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.config import get_settings
from app.db.migrations import m0001_core
from app.db.migrations.schema import sync_collection, sync_validator

VERSION = 4
NAME = "admin_price_history_unlinked_cards"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}

PRICE_HISTORY = {
    "bsonType": "object",
    "required": [
        "price_id",
        "price_per_kwh_vnd",
        "effective_from",
        "effective_to",
        "created_at",
    ],
    "properties": {
        "price_id": STRING,
        "price_per_kwh_vnd": {"bsonType": ["int", "long"], "minimum": 1},
        "effective_from": DATE,
        # null for the price in force; sessions snapshot it when they start.
        "effective_to": NULLABLE_DATE,
        "created_by": NULLABLE_STRING,
        "created_by_name": NULLABLE_STRING,
        "note": NULLABLE_STRING,
        "created_at": DATE,
    },
}

USERS = copy.deepcopy(m0001_core.USERS)
USERS["properties"]["phone"] = {"bsonType": ["string", "null"], "maxLength": 32}

RFID_CARDS = copy.deepcopy(m0001_core.RFID_CARDS)
RFID_CARDS["required"] = [f for f in RFID_CARDS["required"] if f != "user_id"]
# A card can exist without an owner: registered at a station but not yet
# assigned, or unlinked by an admin.
RFID_CARDS["properties"]["user_id"] = NULLABLE_STRING


async def up(db) -> None:
    await sync_validator(db, "users", USERS)
    await sync_validator(db, "rfid_cards", RFID_CARDS)

    await sync_collection(
        db,
        "price_history",
        PRICE_HISTORY,
        [
            IndexModel([("price_id", ASCENDING)], unique=True, name="prices_id_unique"),
            IndexModel([("effective_from", DESCENDING)], name="prices_effective_from"),
            # Only one price can be in force at a time.
            IndexModel(
                [("effective_to", ASCENDING)],
                unique=True,
                name="prices_one_current",
                partialFilterExpression={"effective_to": None},
            ),
        ],
    )

    if await db.price_history.count_documents({}) == 0:
        now = datetime.now(UTC)
        await db.price_history.insert_one(
            {
                "price_id": str(uuid4()),
                "price_per_kwh_vnd": get_settings().price_per_kwh_vnd,
                "effective_from": now,
                "effective_to": None,
                "created_by": None,
                "created_by_name": "system",
                "note": "Giá khởi tạo theo Design Baseline v1.2",
                "created_at": now,
            }
        )
