"""The signed-in user's notification inbox (member, technician and admin apps)."""

from fastapi import APIRouter, HTTPException, Query, status

from app.core.dependencies import CurrentUser
from app.core.exceptions import ResourceNotFoundError
from app.modules.notifications.schemas import (
    MarkAllReadResponse,
    NotificationPage,
    NotificationResponse,
    UnreadCountResponse,
)
from app.modules.notifications.service import (
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationPage)
async def get_notifications(
    user: CurrentUser,
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await list_notifications(
        user_id=user["user_id"],
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(user: CurrentUser):
    return {"unread_count": await unread_count(user_id=user["user_id"])}


@router.post("/read-all", response_model=MarkAllReadResponse)
async def read_all(user: CurrentUser):
    return {"updated": await mark_all_read(user_id=user["user_id"])}


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def read_one(notification_id: str, user: CurrentUser):
    try:
        return await mark_read(user_id=user["user_id"], notification_id=notification_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
