from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import get_settings

settings = get_settings()
client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(settings.mongo_uri, tz_aware=True)
db = client[settings.mongo_database]


async def check_database() -> None:
    await client.admin.command("ping")


async def close_database() -> None:
    await client.close()


def get_collection(name: str):
    return db[name]
