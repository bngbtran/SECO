"""DB-01: users, vehicles, rfid_cards, charging_stations."""

from pymongo import ASCENDING, IndexModel

from app.db.migrations.schema import sync_collection

VERSION = 1
NAME = "core_users_vehicles_rfid_stations"

STRING = {"bsonType": "string"}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}
BOOL = {"bsonType": "bool"}
MONEY = {"bsonType": ["int", "long"], "minimum": 0}

USERS = {
    "bsonType": "object",
    "required": [
        "user_id",
        "name",
        "email",
        "password_hash",
        "role",
        "balance_vnd",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "user_id": STRING,
        "name": {"bsonType": "string", "minLength": 1},
        "email": {"bsonType": "string", "minLength": 3},
        "password_hash": {"bsonType": "string", "minLength": 1},
        "role": {"enum": ["USER", "ADMIN", "TECHNICIAN"]},
        "balance_vnd": MONEY,
        # Kept for the v2 wallet hold; always 0 in v1 (DD 1).
        "held_balance_vnd": MONEY,
        # Bumped on logout to revoke issued JWTs.
        "token_version": {"bsonType": ["int", "long"], "minimum": 0},
        # Budget chosen in the member app, applied at the next RFID scan.
        "pending_charging_budget": {
            "bsonType": "object",
            "required": ["station_id", "max_budget_vnd", "created_at", "expires_at"],
            "properties": {
                "station_id": STRING,
                "max_budget_vnd": {"bsonType": ["int", "long"], "minimum": 1},
                "created_at": DATE,
                "expires_at": DATE,
            },
        },
        "is_active": BOOL,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

VEHICLES = {
    "bsonType": "object",
    "required": [
        "vehicle_id",
        "user_id",
        "plate_number",
        "brand",
        "model",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "vehicle_id": STRING,
        "user_id": STRING,
        "plate_number": {"bsonType": "string", "minLength": 1, "maxLength": 32},
        "brand": {"bsonType": "string", "minLength": 1, "maxLength": 64},
        "model": {"bsonType": "string", "minLength": 1, "maxLength": 64},
        "battery_kwh": {"bsonType": ["double", "int", "null"], "minimum": 0},
        "is_active": BOOL,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

RFID_CARDS = {
    "bsonType": "object",
    "required": ["card_id", "user_id", "is_active", "pending", "created_at", "updated_at"],
    "properties": {
        "card_id": STRING,
        # null while the card is pending registration (FR02).
        "uid": NULLABLE_STRING,
        "user_id": STRING,
        "label": NULLABLE_STRING,
        "is_active": BOOL,
        "pending": BOOL,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

CHARGING_STATIONS = {
    "bsonType": "object",
    "required": [
        "station_id",
        "name",
        "status",
        "is_online",
        "connector_lock",
        "current_session_id",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "station_id": STRING,
        "name": {"bsonType": "string", "minLength": 1},
        "address": NULLABLE_STRING,
        "status": {"enum": ["READY", "CHARGING", "ERROR", "MAINTENANCE"]},
        "is_online": BOOL,
        # Mock stations for the map; no hardware behind them.
        "is_simulated": BOOL,
        "connector_lock": {"enum": ["UNLOCKED", "LOCKED", "UNKNOWN"]},
        "current_session_id": NULLABLE_STRING,
        "last_heartbeat_at": NULLABLE_DATE,
        "latest_telemetry": {"bsonType": ["object", "null"]},
        "latitude": {"bsonType": ["double", "null"], "minimum": -90, "maximum": 90},
        "longitude": {"bsonType": ["double", "null"], "minimum": -180, "maximum": 180},
        "created_at": DATE,
        "updated_at": DATE,
    },
}


async def up(db) -> None:
    await sync_collection(
        db,
        "users",
        USERS,
        [
            IndexModel([("user_id", ASCENDING)], unique=True, name="users_user_id_unique"),
            IndexModel([("email", ASCENDING)], unique=True, name="users_email_unique"),
        ],
    )

    await sync_collection(
        db,
        "vehicles",
        VEHICLES,
        [
            IndexModel([("vehicle_id", ASCENDING)], unique=True, name="vehicles_id_unique"),
            IndexModel(
                [("plate_number", ASCENDING)],
                unique=True,
                name="vehicles_plate_unique",
            ),
            IndexModel([("user_id", ASCENDING)], name="vehicles_user"),
        ],
    )

    await sync_collection(
        db,
        "rfid_cards",
        RFID_CARDS,
        [
            IndexModel([("card_id", ASCENDING)], unique=True, name="rfid_card_id_unique"),
            IndexModel(
                [("uid", ASCENDING)],
                unique=True,
                name="rfid_uid_unique",
                partialFilterExpression={"uid": {"$type": "string"}},
            ),
            IndexModel([("user_id", ASCENDING)], name="rfid_user"),
        ],
    )

    await sync_collection(
        db,
        "charging_stations",
        CHARGING_STATIONS,
        [
            IndexModel([("station_id", ASCENDING)], unique=True, name="stations_id_unique"),
        ],
    )
