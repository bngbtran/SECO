"""Apply MongoDB migrations: `python -m app.db.migrate [--status]` from apps/api.

Uses MONGO_URI / MONGO_DATABASE from the environment or apps/api/.env.
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.db.migrations import MIGRATIONS, applied_versions, apply_migrations
from app.db.mongodb import check_database, close_database, db


def _redact(uri: str) -> str:
    # Hide the password of mongodb://user:password@host URIs.
    scheme, sep, rest = uri.partition("://")
    if "@" not in rest:
        return uri
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}{sep}{user}:***@{host}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="only list migrations")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Database: {settings.mongo_database} @ {_redact(settings.mongo_uri)}")

    try:
        await check_database()

        if not args.status:
            ran = await apply_migrations(db)
            print(f"Applied {len(ran)} migration(s)")

        done = await applied_versions(db)
        for migration in MIGRATIONS:
            mark = "x" if migration.VERSION in done else " "
            print(f"  [{mark}] {migration.VERSION:04d} {migration.NAME}")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
