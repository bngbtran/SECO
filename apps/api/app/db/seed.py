"""DB-04 demo data: `python -m app.db.seed [--reset-passwords]` from apps/api.

Seeds 1 real station (ESP32 / Wokwi), mock stations for the map, demo accounts,
RFID cards and vehicles. Safe to re-run on the shared database:

* runtime state (station status, current session, heartbeat, lock) is only set
  when a station is first created, so a running session is never clobbered;
* existing accounts keep their password and balance unless --reset-passwords;
* accounts dropped from the demo set (RETIRED_EMAILS) are removed with their
  RFID cards and vehicles, unless they already have charging history.

Every demo account uses the password DEMO_PASSWORD.
"""

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import hash_password
from app.db.migrate import _redact
from app.db.migrations import apply_migrations
from app.db.mongodb import check_database, close_database, db

DEMO_PASSWORD = "seco"

# (station_id, name, address, lat, lng, simulated, status, is_online)
STATIONS = [
    ("EVCS-01", "SECO Station 01 - FPT Đà Nẵng",
     "Khu đô thị FPT City, Ngũ Hành Sơn, Đà Nẵng", 16.0544, 108.2022,
     False, "READY", False),
    ("EVCS-02", "SECO Cầu Rồng",
     "Đường Trần Hưng Đạo, Sơn Trà, Đà Nẵng", 16.0610, 108.2272,
     True, "READY", True),
    ("EVCS-03", "SECO Biển Mỹ Khê",
     "Đường Võ Nguyên Giáp, Sơn Trà, Đà Nẵng", 16.0600, 108.2470,
     True, "READY", True),
    ("EVCS-04", "SECO Sân bay Đà Nẵng",
     "Đường Duy Tân, Hải Châu, Đà Nẵng", 16.0439, 108.1994,
     True, "MAINTENANCE", True),
    ("EVCS-05", "SECO Ngũ Hành Sơn",
     "Đường Lê Văn Hiến, Ngũ Hành Sơn, Đà Nẵng", 16.0036, 108.2635,
     True, "READY", False),
    ("EVCS-06", "SECO Vincom Hải Châu",
     "Đường Ngô Quyền, Sơn Trà, Đà Nẵng", 16.0717, 108.2236,
     True, "ERROR", True),
]

# (email, name, role, balance_vnd)
USERS = [
    ("admin@seco.com", "SECO Admin", "ADMIN", 0),
    # Team members, one admin account each.
    ("tranbnb@seco.com", "Bùi Ngọc Bảo Trân", "ADMIN", 0),
    ("tunv@seco.com", "Nguyễn Văn Tú", "ADMIN", 0),
    ("nguyenlb@seco.com", "Lê Bảo Nguyên", "ADMIN", 0),
    ("hieuln@seco.com", "Lê Ngọc Hiếu", "ADMIN", 0),
    ("tech@seco.com", "SECO Technician", "TECHNICIAN", 0),
    ("user@seco.com", "SECO Member", "USER", 200_000),
]

# Earlier versions of this seed used @seco.local; those accounts are renamed in
# place so their user_id, balance, RFID cards and history are kept.
LEGACY_DOMAIN = "@seco.local"

# Demo accounts from earlier versions of this seed.
RETIRED_EMAILS = ["member2@seco.local", "member3@seco.local"]

# (card_id, uid, owner email, label, is_active, pending)
RFID_CARDS = [
    ("CARD-USER-01", "A1B2C3D4", "user@seco.com", "Thẻ chính", True, False),
    # Deactivated card: must be rejected at the station (FR01).
    ("CARD-USER-OLD", "D4E5F6A7", "user@seco.com", "Thẻ cũ (đã khóa)", False, False),
    # Registration in progress (FR02): no UID until the card is scanned at a station.
    ("CARD-USER-NEW", None, "user@seco.com", "Thẻ đang đăng ký", True, True),
    ("CARD-TECH-01", "7E1C2A9F", "tech@seco.com", "Thẻ kỹ thuật viên", True, False),
]

# (vehicle_id, owner email, plate, brand, model, battery_kwh)
VEHICLES = [
    ("VEH-USER-01", "user@seco.com", "43A-123.45", "VinFast", "VF e34", 42.0),
]


def now_utc() -> datetime:
    return datetime.now(UTC)


async def seed_stations() -> None:
    now = now_utc()
    for station_id, name, address, lat, lng, simulated, status, online in STATIONS:
        await db.charging_stations.update_one(
            {"station_id": station_id},
            {
                "$set": {
                    "name": name,
                    "address": address,
                    "latitude": lat,
                    "longitude": lng,
                    "is_simulated": simulated,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "station_id": station_id,
                    "status": status,
                    "is_online": online,
                    "connector_lock": "UNLOCKED",
                    "current_session_id": None,
                    "last_heartbeat_at": now if online else None,
                    "created_at": now,
                },
            },
            upsert=True,
        )
    print(f"Stations: {len(STATIONS)} (1 real, {len(STATIONS) - 1} mock)")


async def rename_legacy_emails() -> None:
    renamed = 0
    for email, *_ in USERS:
        legacy = email.split("@")[0] + LEGACY_DOMAIN
        if await db.users.find_one({"email": email}, {"_id": 1}):
            continue
        result = await db.users.update_one(
            {"email": legacy},
            {"$set": {"email": email, "updated_at": now_utc()}},
        )
        renamed += result.modified_count

    if renamed:
        print(f"Renamed to new email domain: {renamed}")


async def seed_users(reset_passwords: bool) -> dict[str, str]:
    now = now_utc()
    password_hash = hash_password(DEMO_PASSWORD)
    ids = {}

    for email, name, role, balance in USERS:
        existing = await db.users.find_one({"email": email}, {"user_id": 1})
        user_id = existing["user_id"] if existing else str(uuid4())
        ids[email] = user_id

        update = {
            "$set": {"name": name, "role": role, "is_active": True, "updated_at": now},
            "$setOnInsert": {
                "user_id": user_id,
                "email": email,
                "balance_vnd": balance,
                "held_balance_vnd": 0,
                "token_version": 0,
                "created_at": now,
            },
        }
        if reset_passwords or not existing:
            update["$set"]["password_hash"] = password_hash

        await db.users.update_one({"email": email}, update, upsert=True)

    print(f"Users: {len(USERS)}")
    return ids


async def retire_users() -> None:
    retired = 0
    for email in RETIRED_EMAILS:
        user = await db.users.find_one({"email": email}, {"user_id": 1})
        if not user:
            continue

        user_id = user["user_id"]
        has_history = await db.charging_sessions.find_one(
            {"user_id": user_id},
            {"_id": 1},
        ) or await db.transactions.find_one({"user_id": user_id}, {"_id": 1})

        if has_history:
            # Keep the account so sessions and transactions stay consistent.
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {"is_active": False, "updated_at": now_utc()}},
            )
            await db.rfid_cards.update_many(
                {"user_id": user_id},
                {"$set": {"is_active": False, "updated_at": now_utc()}},
            )
            print(f"Deactivated {email} (has charging history)")
        else:
            await db.rfid_cards.delete_many({"user_id": user_id})
            await db.vehicles.delete_many({"user_id": user_id})
            await db.users.delete_one({"user_id": user_id})
        retired += 1

    if retired:
        print(f"Retired accounts: {retired}")


async def seed_rfid_cards(user_ids: dict[str, str]) -> None:
    now = now_utc()
    for card_id, uid, email, label, is_active, pending in RFID_CARDS:
        # Match on the UID too, so a card created earlier with a random id is reused.
        match = {"$or": [{"card_id": card_id}, {"uid": uid}]} if uid else {"card_id": card_id}
        existing = await db.rfid_cards.find_one(match, {"card_id": 1})

        await db.rfid_cards.update_one(
            {"card_id": existing["card_id"] if existing else card_id},
            {
                "$set": {
                    "uid": uid,
                    "user_id": user_ids[email],
                    "label": label,
                    "is_active": is_active,
                    "pending": pending,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    print(f"RFID cards: {len(RFID_CARDS)}")


async def seed_vehicles(user_ids: dict[str, str]) -> None:
    now = now_utc()
    for vehicle_id, email, plate, brand, model, battery in VEHICLES:
        await db.vehicles.update_one(
            {"vehicle_id": vehicle_id},
            {
                "$set": {
                    "user_id": user_ids[email],
                    "plate_number": plate,
                    "brand": brand,
                    "model": model,
                    "battery_kwh": battery,
                    "is_active": True,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    print(f"Vehicles: {len(VEHICLES)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help=f"also reset existing demo accounts to the password {DEMO_PASSWORD!r}",
    )
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()
    print(f"Database: {settings.mongo_database} @ {_redact(settings.mongo_uri)}")

    try:
        await check_database()
        await apply_migrations(db)

        await seed_stations()
        await rename_legacy_emails()
        user_ids = await seed_users(args.reset_passwords)
        await retire_users()
        await seed_rfid_cards(user_ids)
        await seed_vehicles(user_ids)

        print(f"\nDemo accounts (password {DEMO_PASSWORD!r}):")
        for email, name, role, _ in USERS:
            print(f"  {role:<10} {email:<22} {name}")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
