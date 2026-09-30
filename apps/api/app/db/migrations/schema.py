import logging
from typing import Any

from pymongo import IndexModel
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

# Mongo error codes raised when an index with the same name or keys already exists
# with a different definition (e.g. a teammate's DB created by an older script).
_INDEX_CONFLICT_CODES = {85, 86}


async def sync_validator(db, name: str, schema: dict[str, Any]) -> None:
    """Create the collection with a strict $jsonSchema validator, or update it."""
    options = {
        "validator": {"$jsonSchema": schema},
        "validationLevel": "strict",
        "validationAction": "error",
    }

    if name in await db.list_collection_names(filter={"name": name}):
        await db.command({"collMod": name, **options})
    else:
        await db.create_collection(name, **options)


async def _drop_conflicting(collection, model: IndexModel) -> None:
    wanted = model.document
    keys = dict(wanted["key"])
    partial = wanted.get("partialFilterExpression")

    async for existing in await collection.list_indexes():
        if existing["name"] == "_id_":
            continue
        same_name = existing["name"] == wanted["name"]
        same_keys = (
            dict(existing["key"]) == keys
            and existing.get("partialFilterExpression") == partial
        )
        if same_name or same_keys:
            logger.warning(
                "Replacing index %s.%s with %s",
                collection.name,
                existing["name"],
                wanted["name"],
            )
            await collection.drop_index(existing["name"])


async def sync_indexes(db, name: str, indexes: list[IndexModel]) -> None:
    collection = db[name]
    for model in indexes:
        try:
            await collection.create_indexes([model])
        except OperationFailure as exc:
            if exc.code not in _INDEX_CONFLICT_CODES:
                raise
            await _drop_conflicting(collection, model)
            await collection.create_indexes([model])


async def sync_collection(
    db,
    name: str,
    schema: dict[str, Any],
    indexes: list[IndexModel],
) -> None:
    await sync_validator(db, name, schema)
    await sync_indexes(db, name, indexes)
