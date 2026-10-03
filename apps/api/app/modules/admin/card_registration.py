"""FR02 / UC-A02: assign or replace a member's RFID card using the admin desk reader.

1. On Member details the admin clicks "Gán thẻ" (or "Đổi thẻ") -> the reader
   receives cmd SCAN and asks for a card.
2. The reader sends the scanned UID on seco/reader/{id}/scan; the web shows it.
3. The admin clicks "Hoàn tất" -> the UID becomes the member's card (a replaced
   card is unlinked) and the reader receives cmd DONE. The member can then tap
   this card at a charging station to start charging.

The desk reader is not a charging station, so it has its own MQTT namespace:

    seco/reader/{reader_id}/cmd     API -> reader  SCAN | DONE | CANCEL
    seco/reader/{reader_id}/scan    reader -> API  {"uid", "registration_id"?}
    seco/reader/{reader_id}/res     API -> reader  result of a scan; with no open
                                                   request a linked card returns
                                                   {"ok": true, "kind": "CARD_INFO",
                                                    "user_name", "balance_vnd", ...}
    seco/reader/{reader_id}/hb      reader -> API  heartbeat
    seco/reader/{reader_id}/status  reader -> API  ONLINE / OFFLINE (last will)
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.dependencies import CurrentAdmin
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.admin.common import http_errors
from app.modules.admin.users import _card_owner, _normalize_uid
from app.modules.charging.realtime import to_jsonable
from app.modules.logs.service import record_audit
from app.websocket.manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["Admin · RFID card registration"])

registrations = get_collection("card_registrations")
readers = get_collection("rfid_readers")
rfid_cards = get_collection("rfid_cards")
users = get_collection("users")

_OPEN = ["WAITING", "SCANNED"]
# A reader silent for longer than this is shown as offline.
READER_STALE_SECONDS = 30


def now_utc() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def reader_topic(reader_id: str, kind: str) -> str:
    return f"seco/reader/{reader_id}/{kind}"


async def publish_reader(reader_id: str, kind: str, payload: dict[str, Any]) -> bool:
    # Imported lazily: app.mqtt.client imports the MQTT handlers, which import this module.
    from app.mqtt.client import mqtt_service

    try:
        await mqtt_service.publish(reader_topic(reader_id, kind), to_jsonable(payload))
    except RuntimeError:
        logger.warning("MQTT not connected; reader message dropped reader=%s", reader_id)
        return False
    return True


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegistrationStart(BaseModel):
    user_id: str
    mode: Literal["ASSIGN", "REPLACE"] = "ASSIGN"
    # Required for REPLACE: the member's card being swapped out.
    replace_card_id: str | None = None


class RegistrationComplete(BaseModel):
    label: str | None = Field(default=None, max_length=64)


class ReaderOut(BaseModel):
    reader_id: str
    is_online: bool
    last_seen_at: datetime | None = None
    firmware: str | None = None


class RegistrationOut(BaseModel):
    registration_id: str
    reader_id: str
    user_id: str
    user_name: str | None = None
    mode: str
    replace_card_id: str | None = None
    replace_uid: str | None = None
    status: str
    uid: str | None
    last_error: str | None = None
    card_id: str | None = None
    created_at: datetime
    expires_at: datetime
    scanned_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Reader presence
# ---------------------------------------------------------------------------


async def get_reader(reader_id: str) -> dict:
    reader = await readers.find_one({"reader_id": reader_id}, {"_id": 0})
    if not reader:
        return {"reader_id": reader_id, "is_online": False, "last_seen_at": None, "firmware": None}
    seen = reader.get("last_seen_at")
    fresh = seen is not None and (now_utc() - _as_utc(seen)).total_seconds() < READER_STALE_SECONDS
    return {**reader, "is_online": bool(reader.get("is_online")) and fresh}


async def mark_reader(reader_id: str, *, online: bool, firmware: str | None = None) -> None:
    now = now_utc()
    update: dict[str, Any] = {"is_online": online, "updated_at": now}
    if online:
        update["last_seen_at"] = now
    if firmware:
        update["firmware"] = firmware
    await readers.update_one({"reader_id": reader_id}, {"$set": update}, upsert=True)
    await manager.broadcast(
        to_jsonable({"type": "rfid_reader_status", **(await get_reader(reader_id))})
    )


# ---------------------------------------------------------------------------
# Registration lifecycle
# ---------------------------------------------------------------------------


async def _view(registration: dict) -> dict:
    owner = await users.find_one({"user_id": registration["user_id"]}, {"_id": 0, "name": 1})
    replaced = None
    if registration.get("replace_card_id"):
        replaced = await rfid_cards.find_one(
            {"card_id": registration["replace_card_id"]},
            {"_id": 0, "uid": 1},
        )
    return {
        **registration,
        "user_name": owner["name"] if owner else None,
        "replace_uid": replaced.get("uid") if replaced else None,
    }


async def _broadcast(registration: dict) -> None:
    await manager.broadcast(
        to_jsonable({"type": "card_registration_updated", **(await _view(registration))})
    )


async def _close(registration: dict, status: str) -> dict | None:
    closed = await registrations.find_one_and_update(
        {"registration_id": registration["registration_id"], "status": {"$in": _OPEN}},
        {"$set": {"status": status, "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if closed:
        await publish_reader(
            closed["reader_id"],
            "cmd",
            {"command": "CANCEL", "registration_id": closed["registration_id"]},
        )
        await _broadcast(closed)
    return closed


async def _expire_if_due(registration: dict) -> dict:
    if registration["status"] in _OPEN and _as_utc(registration["expires_at"]) <= now_utc():
        return await _close(registration, "EXPIRED") or registration
    return registration


async def get_registration(registration_id: str) -> dict:
    registration = await registrations.find_one({"registration_id": registration_id}, {"_id": 0})
    if not registration:
        raise ResourceNotFoundError("Registration not found")
    return await _expire_if_due(registration)


async def start_registration(
    *,
    user_id: str,
    mode: str,
    replace_card_id: str | None,
    actor: dict,
) -> dict:
    owner = await _card_owner(user_id)

    if mode == "REPLACE":
        card = await rfid_cards.find_one({"card_id": replace_card_id or ""}, {"_id": 0, "user_id": 1})
        if not card or card.get("user_id") != user_id:
            raise BusinessRuleError("Thẻ cần đổi không thuộc member này")
    else:
        replace_card_id = None

    reader_id = settings.admin_reader_id

    # The desk reader serves one request at a time; the newest one wins.
    async for previous in registrations.find(
        {"reader_id": reader_id, "status": {"$in": _OPEN}},
        {"_id": 0},
    ):
        await _close(previous, "CANCELLED")

    now = now_utc()
    registration = {
        "registration_id": str(uuid4()),
        "reader_id": reader_id,
        "user_id": user_id,
        "mode": mode,
        "replace_card_id": replace_card_id,
        "status": "WAITING",
        "uid": None,
        "last_error": None,
        "card_id": None,
        "created_by": actor["user_id"],
        "created_at": now,
        "expires_at": now + timedelta(seconds=settings.card_registration_timeout_seconds),
        "scanned_at": None,
        "completed_at": None,
        "updated_at": now,
    }
    try:
        await registrations.insert_one(registration)
    except DuplicateKeyError as exc:
        raise ConflictError("Đầu đọc đang phục vụ một yêu cầu khác, thử lại") from exc
    registration.pop("_id", None)

    await publish_reader(
        reader_id,
        "cmd",
        {
            "command": "SCAN",
            "registration_id": registration["registration_id"],
            "mode": mode,
            "user_name": owner["name"],
            "timeout_s": settings.card_registration_timeout_seconds,
        },
    )
    await record_audit(
        actor=actor,
        action="CARD_SCAN_START",
        target_type="user",
        target_id=user_id,
        metadata={"registration_id": registration["registration_id"], "mode": mode, "reader_id": reader_id},
    )
    return await _view(registration)


async def _reply(reader_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    await publish_reader(reader_id, "res", payload)
    return payload


async def card_info(uid: str) -> dict[str, Any] | None:
    """Owner and wallet of a linked card, for the desk reader screen."""
    card = await rfid_cards.find_one({"uid": uid}, {"_id": 0, "card_id": 1, "user_id": 1, "label": 1, "is_active": 1})
    if not card or not card.get("user_id"):
        return None
    owner = await users.find_one(
        {"user_id": card["user_id"]},
        {"_id": 0, "name": 1, "role": 1, "balance_vnd": 1, "is_active": 1},
    )
    if not owner:
        return None
    return {
        "uid": uid,
        "card_id": card["card_id"],
        "card_label": card.get("label"),
        "card_active": bool(card.get("is_active")),
        "user_name": owner["name"],
        "role": owner["role"],
        "user_active": bool(owner.get("is_active")),
        "balance_vnd": int(owner.get("balance_vnd", 0)),
    }


async def handle_reader_scan(reader_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A card was tapped on the desk reader."""
    uid = _normalize_uid(str(payload.get("uid", "")))
    if not uid:
        return await _reply(reader_id, {"ok": False, "error": "MISSING_UID"})

    query: dict[str, Any] = {"reader_id": reader_id, "status": {"$in": _OPEN}}
    if payload.get("registration_id"):
        query["registration_id"] = str(payload["registration_id"])
    registration = await registrations.find_one(query, {"_id": 0})
    if registration:
        registration = await _expire_if_due(registration)
    if not registration or registration["status"] not in _OPEN:
        # No "Gán thẻ" open: a linked card shows its owner and balance on the reader.
        info = await card_info(uid)
        if info:
            return await _reply(reader_id, {"ok": True, "kind": "CARD_INFO", **info})
        return await _reply(reader_id, {"ok": False, "error": "NO_REQUEST", "uid": uid})

    error = None
    existing = await rfid_cards.find_one({"uid": uid}, {"_id": 0})
    if existing and existing.get("user_id"):
        if existing["user_id"] == registration["user_id"]:
            error = (
                f"Thẻ {uid} là thẻ đang dùng, hãy quẹt thẻ mới"
                if existing["card_id"] == registration.get("replace_card_id")
                else f"Thẻ {uid} đã được gán cho member này"
            )
        else:
            owner = await users.find_one({"user_id": existing["user_id"]}, {"_id": 0, "name": 1})
            error = f"Thẻ {uid} đang thuộc {owner['name'] if owner else 'người khác'}, hãy quẹt thẻ khác"

    now = now_utc()
    if error:
        updated = await registrations.find_one_and_update(
            {"registration_id": registration["registration_id"]},
            {"$set": {"last_error": error, "updated_at": now}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        await _broadcast(updated)
        return await _reply(
            reader_id,
            {"ok": False, "error": "UID_TAKEN", "uid": uid, "message": error},
        )

    # Tapping another card before confirming replaces the previous UID.
    updated = await registrations.find_one_and_update(
        {"registration_id": registration["registration_id"], "status": {"$in": _OPEN}},
        {
            "$set": {
                "status": "SCANNED",
                "uid": uid,
                "last_error": None,
                "scanned_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    await _broadcast(updated)
    view = await _view(updated)
    return await _reply(
        reader_id,
        {"ok": True, "uid": uid, "registration_id": updated["registration_id"], "user_name": view["user_name"]},
    )


async def complete_registration(registration_id: str, label: str | None, actor: dict) -> dict:
    registration = await get_registration(registration_id)
    if registration["status"] == "EXPIRED":
        raise ConflictError("Hết thời gian chờ quẹt thẻ, hãy thử lại")
    if registration["status"] != "SCANNED" or not registration.get("uid"):
        raise BusinessRuleError("Chưa nhận được UID từ đầu đọc")

    await _card_owner(registration["user_id"])
    uid = registration["uid"]
    now = now_utc()

    existing = await rfid_cards.find_one({"uid": uid}, {"_id": 0})
    if existing and existing.get("user_id"):
        raise ConflictError(f"Thẻ {uid} vừa được gán cho người khác")

    if existing:
        # A card unlinked earlier is handed to the new owner.
        card_id = existing["card_id"]
        await rfid_cards.update_one(
            {"card_id": card_id},
            {
                "$set": {
                    "user_id": registration["user_id"],
                    "label": label or existing.get("label"),
                    "is_active": True,
                    "pending": False,
                    "updated_at": now,
                }
            },
        )
    else:
        card_id = f"CARD-{uuid4().hex[:8].upper()}"
        try:
            await rfid_cards.insert_one(
                {
                    "card_id": card_id,
                    "uid": uid,
                    "user_id": registration["user_id"],
                    "label": label,
                    "is_active": True,
                    "pending": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except DuplicateKeyError as exc:
            raise ConflictError(f"Thẻ {uid} vừa được đăng ký") from exc

    replaced_uid = None
    if registration["mode"] == "REPLACE" and registration.get("replace_card_id"):
        # The old card is unlinked so it can no longer start a charge.
        old = await rfid_cards.find_one_and_update(
            {"card_id": registration["replace_card_id"], "user_id": registration["user_id"]},
            {"$set": {"user_id": None, "is_active": False, "updated_at": now}},
            projection={"_id": 0, "uid": 1},
        )
        replaced_uid = old.get("uid") if old else None

    completed = await registrations.find_one_and_update(
        {"registration_id": registration_id, "status": "SCANNED"},
        {"$set": {"status": "COMPLETED", "card_id": card_id, "completed_at": now, "updated_at": now}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    view = await _view(completed)

    await publish_reader(
        registration["reader_id"],
        "cmd",
        {"command": "DONE", "registration_id": registration_id, "uid": uid, "user_name": view["user_name"]},
    )
    await record_audit(
        actor=actor,
        action="CARD_REPLACE" if registration["mode"] == "REPLACE" else "CARD_ASSIGN",
        target_type="rfid_card",
        target_id=card_id,
        metadata={"uid": uid, "user_id": registration["user_id"], "replaced_uid": replaced_uid},
    )
    await _broadcast(completed)
    return view


async def cancel_registration(registration_id: str) -> dict:
    registration = await get_registration(registration_id)
    if registration["status"] in _OPEN:
        registration = await _close(registration, "CANCELLED") or registration
    return await _view(registration)


# ---------------------------------------------------------------------------
# MQTT entry point (seco/reader/{reader_id}/{kind})
# ---------------------------------------------------------------------------


async def handle_reader_message(reader_id: str, kind: str, payload: bytes | str) -> dict | None:
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload

    if kind == "status":
        online = raw.strip().strip('"').upper() == "ONLINE"
        await mark_reader(reader_id, online=online)
        return {"type": "reader_status", "reader_id": reader_id, "online": online}

    data = json.loads(raw) if raw.strip() else {}
    if kind == "hb":
        await mark_reader(reader_id, online=True, firmware=data.get("fw"))
        return {"type": "reader_heartbeat", "reader_id": reader_id}
    if kind == "scan":
        await mark_reader(reader_id, online=True)
        return {"type": "reader_scan", "reader_id": reader_id, "result": await handle_reader_scan(reader_id, data)}
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/card-registrations/reader", response_model=ReaderOut)
async def get_default_reader(admin: CurrentAdmin):
    """Status of the admin desk reader used for card registration."""
    return await get_reader(settings.admin_reader_id)


@router.post("/card-registrations", response_model=RegistrationOut, status_code=201)
async def post_registration(data: RegistrationStart, admin: CurrentAdmin):
    """Ask the desk reader for a card to assign to (or replace for) a member."""
    with http_errors():
        return await start_registration(
            user_id=data.user_id,
            mode=data.mode,
            replace_card_id=data.replace_card_id,
            actor=admin,
        )


@router.get("/card-registrations/{registration_id}", response_model=RegistrationOut)
async def get_registration_route(registration_id: str, admin: CurrentAdmin):
    with http_errors():
        return await _view(await get_registration(registration_id))


@router.post("/card-registrations/{registration_id}/complete", response_model=RegistrationOut)
async def post_complete(registration_id: str, data: RegistrationComplete, admin: CurrentAdmin):
    """Save the scanned UID as the member's card."""
    with http_errors():
        return await complete_registration(registration_id, data.label, admin)


@router.post("/card-registrations/{registration_id}/cancel", response_model=RegistrationOut)
async def post_cancel(registration_id: str, admin: CurrentAdmin):
    with http_errors():
        return await cancel_registration(registration_id)
