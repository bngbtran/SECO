"""Issue a temporary password from the server, for an admin who forgot theirs and
has no other admin to ask. Runs against MONGO_URI / MONGO_DATABASE in apps/api/.env.

    python -m scripts.issue_temp_password --email admin@seco.com

Then open /reset-password on the web admin: email + this temporary password + a new password.
"""

import argparse
import asyncio

from app.db.mongodb import close_database, get_collection
from app.modules.auth.account import issue_temporary_password_by_admin


async def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a temporary password for a SECO account")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    user = await get_collection("users").find_one(
        {"email": args.email.strip().lower()},
        {"_id": 0, "user_id": 1, "role": 1},
    )
    if not user:
        raise SystemExit(f"user not found: {args.email}")

    issued = await issue_temporary_password_by_admin(None, user["user_id"])
    print(f"{args.email} ({user['role']})")
    print(f"Temporary password: {issued['temporary_password']}")
    print(f"Valid until: {issued['expires_at']:%Y-%m-%d %H:%M} UTC")
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
