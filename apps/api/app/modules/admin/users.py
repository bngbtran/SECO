"""UC-A02: manage users and RFID cards (link / unlink a card to a user)."""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.dependencies import CurrentAdmin
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.core.security import hash_password
from app.db.mongodb import get_collection
from app.modules.admin.common import Limit, Offset, http_errors, page
from app.modules.admin.schemas import (
    AdminCard,
    AdminCardBlock,
    AdminCardCreate,
    AdminCardLink,
    AdminCardUpdate,
    AdminUser,
    AdminUserCreate,
    AdminUserDetail,
    AdminUserUpdate,
    Page,
    Role,
    TemporaryPassword,
)
from app.modules.admin.views import card_views, session_views, transaction_views
from app.modules.auth import account
from app.modules.auth.router import revoke_tokens
from app.modules.cards import service as cards
from app.modules.cards.service import LOST
from app.modules.charging.constants import SessionStatus
from app.modules.logs.service import record_audit
from app.modules.notifications import events

router = APIRouter(tags=["Admin · Users & RFID"])

users = get_collection("users")
rfid_cards = get_collection("rfid_cards")
vehicles = get_collection("vehicles")
sessions = get_collection("charging_sessions")
transactions = get_collection("transactions")

_USER_PROJECTION = {"_id": 0, "password_hash": 0, "pending_charging_budget": 0}
# Roles that tap an RFID card at a station.
_CARD_ROLES = {"USER", "TECHNICIAN"}


def now_utc() -> datetime:
    return datetime.now(UTC)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_uid(uid: str) -> str:
    return uid.strip().upper()


def _search(q: str | None, fields: list[str]) -> dict:
    if not q:
        return {}
    pattern = {"$regex": re.escape(q.strip()), "$options": "i"}
    return {"$or": [{field: pattern} for field in fields]}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def _card_counts(user_ids: list[str]) -> dict[str, int]:
    pipeline = [
        {"$match": {"user_id": {"$in": user_ids}}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
    ]
    return {row["_id"]: row["count"] async for row in await rfid_cards.aggregate(pipeline)}


async def _get_user(user_id: str) -> dict:
    user = await users.find_one({"user_id": user_id}, _USER_PROJECTION)
    if not user:
        raise ResourceNotFoundError("User not found")
    return user


async def _ensure_other_active_admin(user: dict) -> None:
    """Never leave the system without an active admin."""
    if user["role"] != "ADMIN" or not user.get("is_active"):
        return
    others = await users.count_documents(
        {"role": "ADMIN", "is_active": True, "user_id": {"$ne": user["user_id"]}}
    )
    if others == 0:
        raise BusinessRuleError("Cannot remove the last active admin")


async def list_users(
    *,
    q: str | None,
    role: str | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> dict:
    query = _search(q, ["name", "email", "phone", "user_id"])
    if role:
        query["role"] = role
    if is_active is not None:
        query["is_active"] = is_active

    items = await (
        users.find(query, _USER_PROJECTION).sort("created_at", -1).skip(offset).limit(limit)
    ).to_list(length=limit)
    counts = await _card_counts([u["user_id"] for u in items])
    for user in items:
        user["card_count"] = counts.get(user["user_id"], 0)

    return page(items, await users.count_documents(query), limit, offset)


async def get_user_detail(user_id: str) -> dict:
    user = await _get_user(user_id)

    cards = await rfid_cards.find({"user_id": user_id}, {"_id": 0}).to_list(length=None)
    user_vehicles = await vehicles.find({"user_id": user_id}, {"_id": 0}).to_list(length=None)
    recent = await sessions.find({"user_id": user_id}, {"_id": 0}).sort(
        "started_at", -1
    ).limit(10).to_list(length=10)
    open_session = next(
        (
            s
            for s in recent
            if s["status"] in (SessionStatus.ACTIVE.value, SessionStatus.WAITING_PAYMENT.value)
        ),
        None,
    )
    recent_tx = await transactions.find({"user_id": user_id}, {"_id": 0}).sort(
        "created_at", -1
    ).limit(10).to_list(length=10)

    return {
        **user,
        "card_count": len(cards),
        "cards": await card_views(cards),
        "vehicles": user_vehicles,
        "open_session": (await session_views([open_session]))[0] if open_session else None,
        "recent_sessions": await session_views(recent),
        "recent_transactions": await transaction_views(recent_tx),
    }


async def create_user(data: AdminUserCreate, actor: dict) -> dict:
    now = now_utc()
    user = {
        "user_id": str(uuid4()),
        "name": data.name.strip(),
        "email": _normalize_email(data.email),
        "phone": data.phone,
        "password_hash": hash_password(data.password),
        "role": data.role,
        "balance_vnd": 0,
        "held_balance_vnd": 0,
        "token_version": 0,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await users.insert_one(user)
    except DuplicateKeyError as exc:
        raise ConflictError("Email already exists") from exc

    await record_audit(
        actor=actor,
        action="USER_CREATE",
        target_type="user",
        target_id=user["user_id"],
        metadata={"email": user["email"], "role": user["role"]},
    )
    return await _get_user(user["user_id"])


async def update_user(user_id: str, data: AdminUserUpdate, actor: dict) -> dict:
    user = await _get_user(user_id)
    changes = data.model_dump(exclude_unset=True)
    is_self = user_id == actor["user_id"]

    if is_self and (changes.get("is_active") is False or changes.get("role", "ADMIN") != "ADMIN"):
        raise BusinessRuleError("You cannot deactivate or demote your own account")

    losing_admin = changes.get("is_active") is False or (
        "role" in changes and changes["role"] != "ADMIN"
    )
    if losing_admin:
        await _ensure_other_active_admin(user)

    update: dict = {}
    for field in ("name", "phone", "role", "is_active"):
        if field in changes:
            update[field] = changes[field].strip() if field == "name" else changes[field]
    if "email" in changes:
        update["email"] = _normalize_email(changes["email"])
    if changes.get("password"):
        update["password_hash"] = hash_password(changes["password"])

    if not update:
        return user

    update["updated_at"] = now_utc()
    try:
        await users.update_one({"user_id": user_id}, {"$set": update})
    except DuplicateKeyError as exc:
        raise ConflictError("Email already exists") from exc

    # Sign the user out when their access changes.
    if "password_hash" in update or update.get("is_active") is False or "role" in update:
        await revoke_tokens(user_id)
    if update.get("is_active") is False:
        await rfid_cards.update_many(
            {"user_id": user_id},
            {"$set": {"is_active": False, "updated_at": now_utc()}},
        )

    await record_audit(
        actor=actor,
        action="USER_UPDATE",
        target_type="user",
        target_id=user_id,
        metadata={k: v for k, v in changes.items() if k != "password"}
        | ({"password_reset": True} if changes.get("password") else {}),
    )
    return await _get_user(user_id)


async def delete_user(user_id: str, actor: dict) -> None:
    user = await _get_user(user_id)

    if user_id == actor["user_id"]:
        raise BusinessRuleError("You cannot delete your own account")
    await _ensure_other_active_admin(user)

    if await sessions.find_one({"user_id": user_id}, {"_id": 1}) or await transactions.find_one(
        {"user_id": user_id},
        {"_id": 1},
    ):
        raise ConflictError("User has charging history; deactivate the account instead")

    await rfid_cards.update_many(
        {"user_id": user_id},
        {"$set": {"user_id": None, "is_active": False, "updated_at": now_utc()}},
    )
    await vehicles.delete_many({"user_id": user_id})
    await users.delete_one({"user_id": user_id})

    await record_audit(
        actor=actor,
        action="USER_DELETE",
        target_type="user",
        target_id=user_id,
        metadata={"email": user["email"]},
    )


# ---------------------------------------------------------------------------
# RFID cards
# ---------------------------------------------------------------------------


CardStatus = Literal["ACTIVE", "INACTIVE", "PENDING", "UNASSIGNED"]

_CARD_STATUS_QUERY = {
    "ACTIVE": {"is_active": True, "pending": False, "user_id": {"$type": "string"}},
    "INACTIVE": {"is_active": False, "pending": False, "user_id": {"$type": "string"}},
    "PENDING": {"pending": True},
    "UNASSIGNED": {"pending": False, "user_id": None},
}


async def _get_card(card_id: str) -> dict:
    card = await rfid_cards.find_one({"card_id": card_id}, {"_id": 0})
    if not card:
        raise ResourceNotFoundError("RFID card not found")
    return card


async def _card_owner(user_id: str) -> dict:
    owner = await _get_user(user_id)
    if not owner.get("is_active"):
        raise BusinessRuleError("Cannot link a card to an inactive user")
    if owner["role"] not in _CARD_ROLES:
        raise BusinessRuleError("RFID cards are for USER and TECHNICIAN accounts")
    return owner


async def list_cards(
    *,
    q: str | None,
    status_filter: str | None,
    user_id: str | None,
    limit: int,
    offset: int,
) -> dict:
    query = _search(q, ["uid", "card_id", "label"])
    if status_filter:
        query.update(_CARD_STATUS_QUERY[status_filter])
    if user_id:
        query["user_id"] = user_id

    items = await (
        rfid_cards.find(query, {"_id": 0}).sort("created_at", -1).skip(offset).limit(limit)
    ).to_list(length=limit)
    return page(await card_views(items), await rfid_cards.count_documents(query), limit, offset)


async def create_card(data: AdminCardCreate, actor: dict) -> dict:
    if data.user_id:
        await _card_owner(data.user_id)

    now = now_utc()
    pending = data.uid is None
    card = {
        "card_id": f"CARD-{uuid4().hex[:8].upper()}",
        "uid": _normalize_uid(data.uid) if data.uid else None,
        "user_id": data.user_id,
        "label": data.label,
        # Without a UID the card waits for its first scan at a station (FR02).
        "pending": pending,
        "is_active": bool(data.user_id) and not pending,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await rfid_cards.insert_one(card)
    except DuplicateKeyError as exc:
        raise ConflictError("RFID UID already registered") from exc

    await record_audit(
        actor=actor,
        action="CARD_CREATE",
        target_type="rfid_card",
        target_id=card["card_id"],
        metadata={"uid": card["uid"], "user_id": card["user_id"]},
    )
    return (await card_views([await _get_card(card["card_id"])]))[0]


async def update_card(card_id: str, data: AdminCardUpdate, actor: dict) -> dict:
    card = await _get_card(card_id)
    changes = data.model_dump(exclude_unset=True)
    update: dict = {}

    lost = card.get("block_reason") == LOST
    if "label" in changes:
        update["label"] = changes["label"]
    if changes.get("uid"):
        update["uid"] = _normalize_uid(changes["uid"])
        update["pending"] = False
        if card.get("user_id"):
            update["is_active"] = True
    if "is_active" in changes:
        if changes["is_active"] and not card.get("user_id"):
            raise BusinessRuleError("Link the card to a user before activating it")
        if changes["is_active"] and card.get("pending") and "uid" not in update:
            raise BusinessRuleError("Card has no UID yet")
        if changes["is_active"] and lost and "uid" not in update:
            raise BusinessRuleError("Card was reported lost; give the member a new card instead")
        update["is_active"] = changes["is_active"]
    if update.get("is_active") and card.get("block_reason"):
        # Re-activating clears the owner's lock (or the lost flag, with a new UID).
        update.update({"block_reason": None, "blocked_at": None, "blocked_by": None})

    if not update:
        return (await card_views([card]))[0]

    update["updated_at"] = now_utc()
    try:
        await rfid_cards.update_one({"card_id": card_id}, {"$set": update})
    except DuplicateKeyError as exc:
        raise ConflictError("RFID UID already registered") from exc

    await record_audit(
        actor=actor,
        action="CARD_UPDATE",
        target_type="rfid_card",
        target_id=card_id,
        metadata=changes,
    )
    updated = await _get_card(card_id)
    await _notify_card_owner(card, updated)
    return (await card_views([updated]))[0]


async def _notify_card_owner(before: dict, after: dict) -> None:
    if not after.get("user_id") or before.get("is_active") == after.get("is_active"):
        return
    owner = await users.find_one({"user_id": after["user_id"]}, {"_id": 0, "user_id": 1, "role": 1})
    if not owner:
        return
    if after.get("is_active"):
        await events.card_unlocked(owner, after)
    else:
        await events.card_blocked(owner, after, reason="DISABLED", by_admin=True)


async def link_card(card_id: str, user_id: str, actor: dict) -> dict:
    card = await _get_card(card_id)
    owner = await _card_owner(user_id)
    if card.get("block_reason") == LOST:
        raise BusinessRuleError("Card was reported lost and cannot be linked again")

    updated = await rfid_cards.find_one_and_update(
        {"card_id": card_id},
        {
            "$set": {
                "user_id": user_id,
                "is_active": not card.get("pending", False),
                # A new owner starts without the previous owner's lock.
                "block_reason": None,
                "blocked_at": None,
                "blocked_by": None,
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    await record_audit(
        actor=actor,
        action="CARD_LINK",
        target_type="rfid_card",
        target_id=card_id,
        metadata={"user_id": user_id, "user_name": owner["name"], "uid": card.get("uid")},
    )
    return (await card_views([updated]))[0]


async def unlink_card(card_id: str, actor: dict) -> dict:
    card = await _get_card(card_id)
    if not card.get("user_id"):
        raise BusinessRuleError("Card is not linked to any user")

    updated = await rfid_cards.find_one_and_update(
        {"card_id": card_id},
        {"$set": {"user_id": None, "is_active": False, "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    await record_audit(
        actor=actor,
        action="CARD_UNLINK",
        target_type="rfid_card",
        target_id=card_id,
        metadata={"previous_user_id": card["user_id"], "uid": card.get("uid")},
    )
    return (await card_views([updated]))[0]


async def delete_card(card_id: str, actor: dict) -> None:
    card = await _get_card(card_id)
    await rfid_cards.delete_one({"card_id": card_id})
    await record_audit(
        actor=actor,
        action="CARD_DELETE",
        target_type="rfid_card",
        target_id=card_id,
        metadata={"uid": card.get("uid"), "user_id": card.get("user_id")},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/users", response_model=Page[AdminUser])
async def get_users(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    q: str | None = None,
    role: Role | None = None,
    is_active: bool | None = None,
):
    return await list_users(q=q, role=role, is_active=is_active, limit=limit, offset=offset)


@router.post("/users", response_model=AdminUser, status_code=status.HTTP_201_CREATED)
async def post_user(data: AdminUserCreate, admin: CurrentAdmin):
    with http_errors():
        return await create_user(data, admin)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(user_id: str, admin: CurrentAdmin):
    with http_errors():
        return await get_user_detail(user_id)


@router.patch("/users/{user_id}", response_model=AdminUser)
async def patch_user(user_id: str, data: AdminUserUpdate, admin: CurrentAdmin):
    with http_errors():
        return await update_user(user_id, data, admin)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user(user_id: str, admin: CurrentAdmin):
    with http_errors():
        await delete_user(user_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rfid-cards", response_model=Page[AdminCard])
async def get_cards(
    admin: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
    q: str | None = None,
    status: CardStatus | None = None,
    user_id: str | None = None,
):
    return await list_cards(
        q=q,
        status_filter=status,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


@router.post("/rfid-cards", response_model=AdminCard, status_code=status.HTTP_201_CREATED)
async def post_card(data: AdminCardCreate, admin: CurrentAdmin):
    with http_errors():
        return await create_card(data, admin)


@router.patch("/rfid-cards/{card_id}", response_model=AdminCard)
async def patch_card(card_id: str, data: AdminCardUpdate, admin: CurrentAdmin):
    with http_errors():
        return await update_card(card_id, data, admin)


@router.put("/rfid-cards/{card_id}/link", response_model=AdminCard)
async def put_card_link(card_id: str, data: AdminCardLink, admin: CurrentAdmin):
    """Assign the card to a user (UC-A02 "add user vào thẻ")."""
    with http_errors():
        return await link_card(card_id, data.user_id, admin)


@router.delete("/rfid-cards/{card_id}/link", response_model=AdminCard)
async def delete_card_link(card_id: str, admin: CurrentAdmin):
    """Detach the card from its user (UC-A02 "gỡ link thẻ"); the card is deactivated."""
    with http_errors():
        return await unlink_card(card_id, admin)


@router.delete("/rfid-cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_card(card_id: str, admin: CurrentAdmin):
    with http_errors():
        await delete_card(card_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rfid-cards/{card_id}/block", response_model=AdminCard)
async def block_card(card_id: str, data: AdminCardBlock, admin: CurrentAdmin):
    """Lock the card (LOCKED) or mark it lost (LOST) for its owner. Only the card is
    affected: the account stays active. Unlock a LOCKED card with PATCH is_active=true;
    a LOST card is replaced with a new one (card registration REPLACE)."""
    with http_errors():
        card = await cards.admin_block_card(admin, card_id, data.reason)
    return (await card_views([card]))[0]


@router.post("/users/{user_id}/temporary-password", response_model=TemporaryPassword)
async def post_temporary_password(user_id: str, admin: CurrentAdmin):
    """Give the person a temporary password (e.g. an admin who forgot theirs). They use it
    as the current password in /auth/reset-password; their password works until then."""
    with http_errors():
        return await account.issue_temporary_password_by_admin(admin, user_id)
