import argparse
import asyncio
from datetime import datetime, timezone
from getpass import getpass
from uuid import uuid4

from app.core.security import hash_password
from app.db.mongodb import close_database, get_collection


ROLES = {"USER", "ADMIN", "TECHNICIAN"}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update a SECO development user")
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=sorted(ROLES), default="USER")
    parser.add_argument("--balance", type=int, default=0)
    args = parser.parse_args()

    if args.balance < 0:
        raise SystemExit("balance must be >= 0")

    password = getpass("Password: ")
    if not password:
        raise SystemExit("password cannot be empty")

    users = get_collection("users")
    now = datetime.now(timezone.utc)
    email = args.email.strip().lower()

    existing = await users.find_one({"email": email}, {"user_id": 1})
    user_id = existing["user_id"] if existing else str(uuid4())

    await users.update_one(
        {"email": email},
        {
            "$set": {
                "user_id": user_id,
                "name": args.name.strip(),
                "password_hash": hash_password(password),
                "role": args.role,
                "balance_vnd": args.balance,
                "is_active": True,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

    print(f"Seeded user: {email} ({args.role})")
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
