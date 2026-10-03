"""In-app notifications: every user's inbox, pushed live over the WebSocket.

A notification is stored first (the app reads its inbox when it opens) and then
sent to the recipient's own WebSocket connections as
{"type": "notification", "notification": {...}}. Sending is best effort: a
failed notification never undoes the action that triggered it.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from app.core.exceptions import ResourceNotFoundError
from app.db.mongodb import get_collection
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

notifications = get_collection("notifications")
users = get_collection("users")

_PROJECTION = {"_id": 0}


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _push(notification: dict) -> None:
    await manager.send_to_user(
        notification["user_id"],
        {"type": "notification", "notification": jsonable_encoder(notification)},
    )


async def notify(
    *,
    user_id: str,
    role: str,
    type: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict | None:
    document = {
        "notification_id": str(uuid4()),
        "user_id": user_id,
        "role": role,
        "type": type,
        "title": title,
        "body": body,
        "data": jsonable_encoder(data or {}),
        "read_at": None,
        "created_at": now_utc(),
    }
    try:
        await notifications.insert_one(document)
        document.pop("_id", None)
        await _push(document)
    except Exception:
        logger.exception("Failed to notify user=%s type=%s", user_id, type)
        return None
    return document


async def notify_user(user: dict, **kwargs) -> dict | None:
    return await notify(user_id=user["user_id"], role=user["role"], **kwargs)


async def notify_role(role: str, **kwargs) -> int:
    """Notify every active user with this role (e.g. all technicians)."""
    sent = 0
    try:
        async for user in users.find(
            {"role": role, "is_active": True},
            {"_id": 0, "user_id": 1, "role": 1},
        ):
            if await notify_user(user, **kwargs):
                sent += 1
    except Exception:
        logger.exception("Failed to notify role=%s", role)
    return sent


async def list_notifications(
    *,
    user_id: str,
    unread_only: bool,
    limit: int,
    offset: int,
) -> dict:
    query: dict[str, Any] = {"user_id": user_id}
    if unread_only:
        query["read_at"] = None

    items = [
        item
        async for item in notifications.find(query, _PROJECTION)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    ]
    return {
        "items": items,
        "total": await notifications.count_documents(query),
        "unread_count": await unread_count(user_id=user_id),
        "limit": limit,
        "offset": offset,
    }


async def unread_count(*, user_id: str) -> int:
    return await notifications.count_documents({"user_id": user_id, "read_at": None})


async def mark_read(*, user_id: str, notification_id: str) -> dict:
    # Reading twice keeps the first read time.
    await notifications.update_one(
        {"notification_id": notification_id, "user_id": user_id, "read_at": None},
        {"$set": {"read_at": now_utc()}},
    )
    notification = await notifications.find_one(
        {"notification_id": notification_id, "user_id": user_id},
        _PROJECTION,
    )
    if not notification:
        raise ResourceNotFoundError("Notification not found")
    return notification


async def mark_all_read(*, user_id: str) -> int:
    result = await notifications.update_many(
        {"user_id": user_id, "read_at": None},
        {"$set": {"read_at": now_utc()}},
    )
    return result.modified_count
