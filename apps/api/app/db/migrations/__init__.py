"""Versioned MongoDB migrations (collections, $jsonSchema validators, indexes).

Each migration is idempotent and recorded in `schema_migrations`, so it runs once
per database. To change the schema, add a new `mNNNN_*.py` module with a higher
VERSION and append it to MIGRATIONS; never edit an applied migration.
"""

import logging
from datetime import UTC, datetime
from types import ModuleType

from pymongo.errors import DuplicateKeyError

from app.db.migrations import m0001_core, m0002_charging, m0003_logs, m0004_admin

logger = logging.getLogger(__name__)

MIGRATIONS: list[ModuleType] = [m0001_core, m0002_charging, m0003_logs, m0004_admin]

LEDGER = "schema_migrations"


async def applied_versions(db) -> set[int]:
    return {doc["version"] async for doc in db[LEDGER].find({}, {"version": 1})}


async def pending_migrations(db) -> list[ModuleType]:
    done = await applied_versions(db)
    return [m for m in MIGRATIONS if m.VERSION not in done]


async def apply_migrations(db=None) -> list[ModuleType]:
    """Apply pending migrations in order and return the ones that ran."""
    if db is None:
        from app.db.mongodb import db as default_db

        db = default_db

    await db[LEDGER].create_index("version", unique=True, name="schema_migrations_version")

    ran = []
    for migration in await pending_migrations(db):
        logger.info("Applying migration %04d %s", migration.VERSION, migration.NAME)
        await migration.up(db)
        try:
            await db[LEDGER].insert_one(
                {
                    "version": migration.VERSION,
                    "name": migration.NAME,
                    "applied_at": datetime.now(UTC),
                }
            )
        except DuplicateKeyError:
            # Another API instance applied it concurrently; migrations are idempotent.
            pass
        ran.append(migration)

    return ran
