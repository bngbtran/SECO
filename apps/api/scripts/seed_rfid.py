import argparse
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.db.mongodb import close_database, get_collection


async def main() -> None:
    parser = argparse.ArgumentParser(description="Link an active RFID card to a SECO development user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--uid", required=True, help="RFID UID as sent by the station, e.g. A1B2C3D4")
    args = parser.parse_args()

    users = get_collection("users")
    rfid_cards = get_collection("rfid_cards")
    now = datetime.now(timezone.utc)

    user = await users.find_one({"email": args.email.strip().lower()}, {"user_id": 1})
    if not user:
        raise SystemExit(f"user not found: {args.email}")

    uid = args.uid.strip()
    existing = await rfid_cards.find_one({"uid": uid}, {"card_id": 1})
    card_id = existing["card_id"] if existing else str(uuid4())

    await rfid_cards.update_one(
        {"card_id": card_id},
        {
            "$set": {
                "uid": uid,
                "user_id": user["user_id"],
                "is_active": True,
                "pending": False,
                "updated_at": now,
            },
            "$setOnInsert": {
                "card_id": card_id,
                "created_at": now,
            },
        },
        upsert=True,
    )

    print(f"Seeded RFID {uid} -> {args.email}")
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
