from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.billing.settlement import settle_member_session
from app.modules.charging.service import complete_session, create_member_session

rfid_cards = get_collection("rfid_cards")
users = get_collection("users")


async def verify_member_rfid(
    *,
    uid: str,
) -> tuple[dict, dict]:
    card = await rfid_cards.find_one(
        {
            "uid": uid.strip(),
            "is_active": True,
            "pending": False,
        },
        {"_id": 0},
    )

    if not card:
        raise ResourceNotFoundError("RFID card not found or inactive")

    user_id = card.get("user_id")
    if not user_id:
        raise BusinessRuleError("RFID card is not linked to a user")

    user = await users.find_one(
        {
            "user_id": user_id,
        },
        {"_id": 0},
    )

    if not user:
        raise ResourceNotFoundError("RFID card owner not found")

    if not user.get("is_active"):
        raise BusinessRuleError("RFID card owner is inactive")

    if user.get("role") != "USER":
        raise BusinessRuleError("RFID card owner is not a USER")

    return card, user


async def start_member_charging(
    *,
    uid: str,
    station_id: str,
    max_budget_vnd: int | None,
) -> dict:
    _, user = await verify_member_rfid(uid=uid)

    session = await create_member_session(
        user_id=user["user_id"],
        station_id=station_id,
        max_budget_vnd=max_budget_vnd,
    )

    return {
        "session": session,
        "user": user,
    }

async def get_member_session(
    *,
    user_id: str,
    session_id: str,
) -> dict:
    session = await get_collection("charging_sessions").find_one(
        {
            "session_id": session_id,
            "user_id": user_id,
            "type": "MEMBER",
        },
        {"_id": 0},
    )

    if not session:
        raise ResourceNotFoundError("Member charging session not found")

    return session


async def stop_member_charging(
    *,
    user_id: str,
    session_id: str,
) -> tuple[dict, dict]:
    session = await get_member_session(
        user_id=user_id,
        session_id=session_id,
    )

    status = session.get("status")

    if status == "COMPLETED":
        transaction = await get_collection("transactions").find_one(
            {
                "session_id": session_id,
                "type": "CHARGING",
                "status": "COMPLETED",
            },
            {"_id": 0},
        )

        return session, transaction

    if status != "ACTIVE":
        raise BusinessRuleError(
            f"Cannot stop member session in status {status}"
        )

    await complete_session(
        session_id=session_id,
        stop_reason="UNPLUG",
    )

    transaction = await settle_member_session(
        session_id=session_id,
    )

    updated_session = await get_member_session(
        user_id=user_id,
        session_id=session_id,
    )

    return updated_session, transaction