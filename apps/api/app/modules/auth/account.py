"""Self-service account actions: profile and change password (every role), forgot password."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.core.security import hash_password, verify_password
from app.db.mongodb import get_collection
from app.modules.logs.service import record_audit
from app.modules.notifications import events

logger = logging.getLogger(__name__)
settings = get_settings()

users = get_collection("users")
password_resets = get_collection("password_resets")

_USER_PROJECTION = {"_id": 0, "password_hash": 0, "pending_charging_budget": 0}
INVALID_TEMP_PASSWORD = "Invalid or expired temporary password"


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _get_user(user_id: str) -> dict:
    user = await users.find_one({"user_id": user_id}, _USER_PROJECTION)
    if not user:
        raise ResourceNotFoundError("User not found")
    return user


async def _set_password(user_id: str, new_password: str) -> dict:
    """Store the new hash and revoke every token issued before (all devices)."""
    return await users.find_one_and_update(
        {"user_id": user_id},
        {
            "$set": {"password_hash": hash_password(new_password), "updated_at": now_utc()},
            "$inc": {"token_version": 1},
        },
        projection=_USER_PROJECTION,
        return_document=ReturnDocument.AFTER,
    )


async def update_profile(user: dict, changes: dict) -> dict:
    """Name and phone only: email is the login and role / balance are not the user's to change."""
    update = {}
    if "name" in changes:
        update["name"] = changes["name"].strip()
        if not update["name"]:
            raise BusinessRuleError("Name must not be empty")
    if "phone" in changes:
        update["phone"] = changes["phone"].strip() if changes["phone"] else None

    if not update:
        return await _get_user(user["user_id"])

    update["updated_at"] = now_utc()
    await users.update_one({"user_id": user["user_id"]}, {"$set": update})
    await record_audit(
        actor=user,
        action="PROFILE_UPDATE",
        target_type="user",
        target_id=user["user_id"],
        metadata={k: v for k, v in update.items() if k != "updated_at"},
    )
    return await _get_user(user["user_id"])


async def change_password(user: dict, current_password: str, new_password: str) -> dict:
    stored = await users.find_one({"user_id": user["user_id"]}, {"_id": 0, "password_hash": 1})
    if not stored or not verify_password(current_password, stored["password_hash"]):
        raise BusinessRuleError("Current password is incorrect")
    if current_password == new_password:
        raise BusinessRuleError("New password must be different from the current one")

    updated = await _set_password(user["user_id"], new_password)
    await record_audit(
        actor=user,
        action="PASSWORD_CHANGE",
        target_type="user",
        target_id=user["user_id"],
    )
    await events.password_changed(updated, via_reset=False)
    return updated


# No look-alike characters (0/O, 1/l/I): the user types it from the screen.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TEMP_PASSWORD_LENGTH = 10
# Only accounts that use the mobile app; an admin is reset by another admin.
SELF_RESET_ROLES = {"USER", "TECHNICIAN"}


def _temporary_password() -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


async def request_password_reset(email: str) -> dict:
    """Issue a temporary password, shown to the user, that stands in for the old one.

    The account password is not touched yet: the user still signs in with it
    until they finish the reset, so someone else asking for a reset cannot lock
    the owner out. Asking again replaces the previous temporary password.
    """
    user = await users.find_one(
        {"email": email.strip().lower(), "is_active": True},
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "role": 1},
    )
    if not user:
        raise ResourceNotFoundError("Account not found")
    if user["role"] not in SELF_RESET_ROLES:
        raise BusinessRuleError("Admin passwords are reset by another admin")

    now = now_utc()
    await password_resets.update_many(
        {"user_id": user["user_id"], "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"expires_at": now}},
    )

    temporary_password = _temporary_password()
    expires_at = now + timedelta(minutes=settings.password_reset_ttl_minutes)
    await password_resets.insert_one(
        {
            "reset_id": str(uuid4()),
            "user_id": user["user_id"],
            "temp_password_hash": hash_password(temporary_password),
            "attempts": 0,
            "expires_at": expires_at,
            "used_at": None,
            "created_at": now,
        }
    )
    await record_audit(
        actor=user,
        action="PASSWORD_RESET_REQUEST",
        target_type="user",
        target_id=user["user_id"],
    )
    return {"temporary_password": temporary_password, "expires_at": expires_at}


async def reset_password(email: str, temporary_password: str, new_password: str) -> dict:
    """Like change password, with the temporary password as the current one."""
    user = await users.find_one(
        {"email": email.strip().lower(), "is_active": True},
        _USER_PROJECTION,
    )
    if not user or user["role"] not in SELF_RESET_ROLES:
        raise BusinessRuleError(INVALID_TEMP_PASSWORD)

    reset = await password_resets.find_one(
        {"user_id": user["user_id"], "used_at": None, "expires_at": {"$gt": now_utc()}},
        sort=[("created_at", -1)],
    )
    if not reset or reset["attempts"] >= settings.password_reset_max_attempts:
        raise BusinessRuleError(INVALID_TEMP_PASSWORD)

    if not verify_password(temporary_password.strip(), reset["temp_password_hash"]):
        await password_resets.update_one({"reset_id": reset["reset_id"]}, {"$inc": {"attempts": 1}})
        raise BusinessRuleError(INVALID_TEMP_PASSWORD)
    if temporary_password.strip() == new_password:
        raise BusinessRuleError("New password must be different from the temporary one")

    # One use only, even if two requests arrive together.
    claimed = await password_resets.find_one_and_update(
        {"reset_id": reset["reset_id"], "used_at": None},
        {"$set": {"used_at": now_utc()}},
    )
    if not claimed:
        raise BusinessRuleError(INVALID_TEMP_PASSWORD)

    updated = await _set_password(user["user_id"], new_password)
    await record_audit(
        actor=user,
        action="PASSWORD_RESET",
        target_type="user",
        target_id=user["user_id"],
    )
    await events.password_changed(updated, via_reset=True)
    return updated
