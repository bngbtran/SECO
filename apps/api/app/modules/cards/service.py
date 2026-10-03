"""A card owner (member or technician) locks, unlocks or reports their RFID card lost.

LOCKED  the owner's temporary lock; the owner can unlock it from the app.
LOST    permanent: the card is never activated again, the desk gives a new one
        (card registration REPLACE). An admin cannot re-activate it either.
A blocked card is rejected at the station like any inactive card
(is_active=False). A session already running is not stopped: the vehicle is
plugged in, and the member stops it from the app or the station as usual.
"""

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.logs.service import record_audit
from app.modules.notifications import events

rfid_cards = get_collection("rfid_cards")
users = get_collection("users")

LOCKED = "LOCKED"
LOST = "LOST"


def now_utc() -> datetime:
    return datetime.now(UTC)


def card_state(card: dict) -> str:
    """ACTIVE | LOCKED | LOST | INACTIVE (disabled by an admin) | PENDING (no UID yet)."""
    if card.get("block_reason") in (LOCKED, LOST):
        return card["block_reason"]
    if card.get("pending"):
        return "PENDING"
    return "ACTIVE" if card.get("is_active") else "INACTIVE"


def card_view(card: dict) -> dict:
    uid = card.get("uid") or ""
    return {
        "card_id": card["card_id"],
        # Only the last digits: the full UID is what a cloned card would need.
        "uid_suffix": uid[-4:] if uid else None,
        "label": card.get("label"),
        "status": card_state(card),
        "blocked_at": card.get("blocked_at"),
        "created_at": card["created_at"],
        "updated_at": card["updated_at"],
    }


async def list_my_cards(*, user_id: str) -> list[dict]:
    cards = rfid_cards.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    return [card_view(card) async for card in cards]


async def _my_card(user: dict, card_id: str) -> dict:
    card = await rfid_cards.find_one({"card_id": card_id, "user_id": user["user_id"]}, {"_id": 0})
    if not card:
        raise ResourceNotFoundError("RFID card not found")
    if card.get("pending"):
        raise BusinessRuleError("Card has not been scanned yet")
    return card


async def _block(actor: dict, card: dict, reason: str, *, owner: dict | None = None) -> dict:
    """actor locks the card; owner (when an admin acts for them) gets the notification."""
    updated = await rfid_cards.find_one_and_update(
        {"card_id": card["card_id"]},
        {
            "$set": {
                "is_active": False,
                "block_reason": reason,
                "blocked_at": now_utc(),
                "blocked_by": actor["user_id"],
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    await record_audit(
        actor=actor,
        action="CARD_REPORT_LOST" if reason == LOST else "CARD_LOCK",
        target_type="rfid_card",
        target_id=card["card_id"],
        metadata={"uid": card.get("uid"), "user_id": card.get("user_id")},
    )
    await events.card_blocked(owner or actor, updated, reason=reason, by_admin=owner is not None)
    return updated


async def admin_block_card(admin: dict, card_id: str, reason: str) -> dict:
    """The desk locks a card or marks it lost for its owner (e.g. the member phoned in).

    Only the card changes: the account and its other cards keep working. The
    owner cannot unlock a card the desk locked.
    """
    card = await rfid_cards.find_one({"card_id": card_id}, {"_id": 0})
    if not card:
        raise ResourceNotFoundError("RFID card not found")
    if not card.get("user_id"):
        raise BusinessRuleError("Card is not linked to any user")
    if card.get("pending"):
        raise BusinessRuleError("Card has not been scanned yet")

    state = card_state(card)
    if state == LOST or (state == reason == LOCKED and card.get("blocked_by") == admin["user_id"]):
        return card
    owner = await users.find_one({"user_id": card["user_id"]}, {"_id": 0, "user_id": 1, "role": 1})
    return await _block(admin, card, reason, owner=owner or {"user_id": card["user_id"], "role": "USER"})


async def lock_card(user: dict, card_id: str) -> dict:
    card = await _my_card(user, card_id)
    state = card_state(card)
    if state == LOCKED:
        return card_view(card)
    if state == LOST:
        raise BusinessRuleError("Card was reported lost")
    if state == "INACTIVE":
        raise BusinessRuleError("Card was disabled by an admin")
    return card_view(await _block(user, card, LOCKED))


async def report_lost(user: dict, card_id: str) -> dict:
    card = await _my_card(user, card_id)
    if card_state(card) == LOST:
        return card_view(card)
    return card_view(await _block(user, card, LOST))


async def unlock_card(user: dict, card_id: str) -> dict:
    card = await _my_card(user, card_id)
    state = card_state(card)
    if state == "ACTIVE":
        return card_view(card)
    if state == LOST:
        raise BusinessRuleError("A card reported lost cannot be unlocked; ask the desk for a new card")
    if state != LOCKED or card.get("blocked_by") != user["user_id"]:
        raise BusinessRuleError("Card was locked by an admin; contact the desk")

    updated = await rfid_cards.find_one_and_update(
        {"card_id": card_id, "block_reason": LOCKED},
        {
            "$set": {
                "is_active": True,
                "block_reason": None,
                "blocked_at": None,
                "blocked_by": None,
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise BusinessRuleError("Card state changed; reload and try again")

    await record_audit(
        actor=user,
        action="CARD_UNLOCK",
        target_type="rfid_card",
        target_id=card_id,
        metadata={"uid": card.get("uid")},
    )
    await events.card_unlocked(user, updated)
    return card_view(updated)
