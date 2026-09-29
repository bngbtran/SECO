from bson import Decimal128
from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    ResourceNotFoundError,
)
from app.db.mongodb import get_collection
from app.modules.member.schemas import (
    MemberChargingResponse,
    MemberIdentityResponse,
    MemberSessionResponse,
    MemberStartChargingRequest,
    RFIDVerifyRequest,
)
from app.modules.member.service import (
    get_member_session,
    start_member_charging,
    stop_member_charging,
    verify_member_rfid,
)
from app.websocket.manager import manager

router = APIRouter(
    prefix="/api/v1/member",
    tags=["Member"],
)

def _to_decimal(value):
    if isinstance(value, Decimal128):
        return value.to_decimal()
    return value

@router.post(
    "/rfid/verify",
    response_model=MemberIdentityResponse,
)
async def verify_rfid(request: RFIDVerifyRequest):
    try:
        card, user = await verify_member_rfid(
            uid=request.uid,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BusinessRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    return MemberIdentityResponse(
        user_id=user["user_id"],
        name=user["name"],
        role=user["role"],
        balance_vnd=int(user.get("balance_vnd", 0)),
        card_id=card["card_id"],
    )


@router.post(
    "/charging/start",
    response_model=MemberChargingResponse,
)
async def start_charging(
    request: MemberStartChargingRequest,
):
    try:
        result = await start_member_charging(
            uid=request.uid,
            station_id=request.station_id,
            max_budget_vnd=request.max_budget_vnd,
        )
        await manager.broadcast(
    {
        "type": "member_session_updated",
        "event": "STARTED",
        "session_id": result["session"]["session_id"],
        "station_id": result["session"]["station_id"],
        "user_id": result["user"]["user_id"],
        "status": result["session"]["status"],
        "cost_vnd": result["session"]["cost_vnd"],
    }
)
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except BusinessRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    session = result["session"]
    user = result["user"]

    return MemberChargingResponse(
        session_id=session["session_id"],
        station_id=session["station_id"],
        user_id=user["user_id"],
        user_name=user["name"],
        role=user["role"],
        balance_vnd=int(user["balance_vnd"]),
        max_budget_vnd=int(session["max_budget_vnd"]),
        status=session["status"],
        energy_kwh=session["energy_kwh"].to_decimal(),
        power_kw=session["power_kw"].to_decimal(),
        price_per_kwh_vnd=int(session["price_per_kwh_vnd"]),
        cost_vnd=int(session["cost_vnd"]),
        started_at=session["started_at"],
    )

@router.post(
    "/charging/{session_id}/stop",
    response_model=MemberSessionResponse,
)

async def stop_charging(
    session_id: str,
    current_user: CurrentUser,
):
    try:
        session, transaction = await stop_member_charging(
            user_id=current_user["user_id"],
            session_id=session_id,
        )
        await manager.broadcast(
            {
                "type": "member_session_updated",
                "event": "STOPPED",
                "session_id": session["session_id"],
                "station_id": session["station_id"],
                "user_id": session["user_id"],
                "status": session["status"],
                "stop_reason": session.get("stop_reason"),
                "cost_vnd": session["cost_vnd"],
                "transaction_id": (
                    transaction["transaction_id"]
                    if transaction
                    else None
                ),
            }
        )
        return MemberSessionResponse(
            session_id=session["session_id"],
            station_id=session["station_id"],
            user_id=session["user_id"],
            user_name=current_user["name"],
            role=current_user["role"],
            balance_vnd=(
    await get_collection("users").find_one(
        {"user_id": current_user["user_id"]},
        {"_id": 0, "balance_vnd": 1},
            )
        )["balance_vnd"],
            max_budget_vnd=session.get("max_budget_vnd"),
            status=session["status"],
            energy_kwh=_to_decimal(session["energy_kwh"]),
            power_kw=_to_decimal(session["power_kw"]),
            price_per_kwh_vnd=session["price_per_kwh_vnd"],
            cost_vnd=session["cost_vnd"],
            started_at=session["started_at"],
            ended_at=session.get("ended_at"),
            stop_reason=session.get("stop_reason"),
            transaction_id=(
                transaction["transaction_id"]
                if transaction
                else None
            ),
            transaction_amount_vnd=(
                transaction["amount_vnd"]
                if transaction
                else None
            ),
            transaction_status=(
                transaction["status"]
                if transaction
                else None
            ),
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BusinessRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

@router.get(
    "/charging/{session_id}",
    response_model=MemberSessionResponse,
)
async def get_charging_session(
    session_id: str,
    current_user: CurrentUser,
):
    try:
        session = await get_member_session(
            user_id=current_user["user_id"],
            session_id=session_id,
        )

        user = await get_collection("users").find_one(
            {"user_id": current_user["user_id"]},
            {
                "_id": 0,
                "name": 1,
                "role": 1,
                "balance_vnd": 1,
            },
        )

        if not user:
            raise ResourceNotFoundError("Member user not found")

        transaction = await get_collection("transactions").find_one(
            {
                "session_id": session_id,
                "type": "CHARGING",
                "status": "COMPLETED",
            },
            {"_id": 0},
        )

        return MemberSessionResponse(
            session_id=session["session_id"],
            station_id=session["station_id"],
            user_id=session["user_id"],
            user_name=user["name"],
            role=user["role"],
            balance_vnd=user["balance_vnd"],
            max_budget_vnd=session.get("max_budget_vnd"),
            status=session["status"],
            energy_kwh=_to_decimal(session["energy_kwh"]),
            power_kw=_to_decimal(session["power_kw"]),
            price_per_kwh_vnd=session["price_per_kwh_vnd"],
            cost_vnd=session["cost_vnd"],
            started_at=session["started_at"],
            ended_at=session.get("ended_at"),
            stop_reason=session.get("stop_reason"),
            transaction_id=(
                transaction["transaction_id"]
                if transaction
                else None
            ),
            transaction_amount_vnd=(
                transaction["amount_vnd"]
                if transaction
                else None
            ),
            transaction_status=(
                transaction["status"]
                if transaction
                else None
            ),
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BusinessRuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    