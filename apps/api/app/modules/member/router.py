from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.core.dependencies import CurrentMember
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    FeatureDisabledError,
    ResourceNotFoundError,
)
from app.core.mongo_types import from_decimal128
from app.db.mongodb import get_collection
from app.modules.billing.settlement import get_transaction_for_session
from app.modules.cards import service as cards
from app.modules.cards.schemas import MyCardResponse
from app.modules.member.favorites import add_favorite, list_favorites, remove_favorite
from app.modules.member.realtime import announce_stopped, broadcast_session
from app.modules.member.schemas import (
    MemberBudgetRequest,
    MemberBudgetResponse,
    MemberChargingResponse,
    MemberIdentityResponse,
    MemberSessionHistoryResponse,
    MemberSessionResponse,
    MemberStartChargingRequest,
    MemberTransactionResponse,
    MemberWalletResponse,
    RFIDVerifyRequest,
)
from app.modules.member.service import (
    clear_pending_budget,
    get_active_pending_budget,
    get_current_member_session,
    get_member_session,
    get_member_transaction,
    get_member_wallet,
    get_pending_budget,
    list_member_sessions,
    list_member_transactions,
    session_live_fields,
    set_pending_budget,
    start_member_charging,
    stop_member_charging,
    verify_member_rfid,
)
from app.modules.notifications import events
from app.modules.payment.topup_schemas import DemoTopupRequest, DemoTopupResponse
from app.modules.payment.topup_service import demo_topup
from app.modules.station.schemas import FavoriteStationResponse
from app.modules.support import service as support
from app.modules.support.schemas import (
    MemberSupportTicket,
    MemberSupportTicketPage,
    SupportTicketCreate,
)

router = APIRouter(
    prefix="/api/v1/member",
    tags=["Member"],
)

users = get_collection("users")

Limit = Query(default=50, ge=1, le=100)
Offset = Query(default=0, ge=0)


@contextmanager
def _http_errors():
    try:
        yield
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FeatureDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _session_response(session: dict, user: dict, transaction: dict | None):
    fresh_user = await users.find_one(
        {"user_id": user["user_id"]},
        {"_id": 0, "balance_vnd": 1},
    )

    return MemberSessionResponse(
        session_id=session["session_id"],
        station_id=session["station_id"],
        user_id=session["user_id"],
        user_name=user["name"],
        role=user["role"],
        balance_vnd=int((fresh_user or user).get("balance_vnd", 0)),
        max_budget_vnd=session.get("max_budget_vnd"),
        status=session["status"],
        energy_kwh=from_decimal128(session["energy_kwh"]),
        power_kw=from_decimal128(session["power_kw"]),
        price_per_kwh_vnd=session["price_per_kwh_vnd"],
        cost_vnd=session["cost_vnd"],
        started_at=session["started_at"],
        ended_at=session.get("ended_at"),
        stop_reason=session.get("stop_reason"),
        **session_live_fields(session),
        transaction_id=transaction["transaction_id"] if transaction else None,
        transaction_amount_vnd=transaction["amount_vnd"] if transaction else None,
        transaction_status=transaction["status"] if transaction else None,
        balance_after_vnd=transaction.get("balance_after_vnd") if transaction else None,
    )


@router.post(
    "/rfid/verify",
    response_model=MemberIdentityResponse,
)
async def verify_rfid(request: RFIDVerifyRequest):
    try:
        card, user = await verify_member_rfid(uid=request.uid)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    pending = get_active_pending_budget(user)

    return MemberIdentityResponse(
        user_id=user["user_id"],
        name=user["name"],
        role=user["role"],
        balance_vnd=int(user.get("balance_vnd", 0)),
        card_id=card["card_id"],
        pending_max_budget_vnd=pending["max_budget_vnd"] if pending else None,
    )


@router.put(
    "/charging/budget",
    response_model=MemberBudgetResponse,
)
async def set_charging_budget(
    request: MemberBudgetRequest,
    current_user: CurrentMember,
):
    with _http_errors():
        return await set_pending_budget(
            user_id=current_user["user_id"],
            station_id=request.station_id,
            max_budget_vnd=request.max_budget_vnd,
        )


@router.get(
    "/charging/budget",
    response_model=MemberBudgetResponse,
)
async def get_charging_budget(current_user: CurrentMember):
    with _http_errors():
        pending = await get_pending_budget(user_id=current_user["user_id"])

    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending charging budget",
        )
    return pending


@router.delete(
    "/charging/budget",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_charging_budget(current_user: CurrentMember):
    await clear_pending_budget(user_id=current_user["user_id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/charging/start",
    response_model=MemberChargingResponse,
)
async def start_charging(
    request: MemberStartChargingRequest,
    current_user: CurrentMember,
):
    with _http_errors():
        result = await start_member_charging(
            uid=request.uid,
            station_id=request.station_id,
            max_budget_vnd=request.max_budget_vnd,
            expected_user_id=current_user["user_id"],
        )

    session = result["session"]
    user = result["user"]

    await broadcast_session(session, "STARTED")

    return MemberChargingResponse(
        session_id=session["session_id"],
        station_id=session["station_id"],
        user_id=user["user_id"],
        user_name=user["name"],
        role=user["role"],
        balance_vnd=int(user.get("balance_vnd", 0)),
        max_budget_vnd=int(session["max_budget_vnd"]),
        status=session["status"],
        energy_kwh=from_decimal128(session["energy_kwh"]),
        power_kw=from_decimal128(session["power_kw"]),
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
    current_user: CurrentMember,
):
    with _http_errors():
        was_active = (
            await get_member_session(user_id=current_user["user_id"], session_id=session_id)
        )["status"] == "ACTIVE"
        session, transaction = await stop_member_charging(
            user_id=current_user["user_id"],
            session_id=session_id,
        )

    if was_active:
        await announce_stopped({"session": session, "transaction": transaction})

    return await _session_response(session, current_user, transaction)


@router.get(
    "/charging/current",
    response_model=MemberSessionResponse,
)
async def get_current_charging_session(current_user: CurrentMember):
    """The member's ACTIVE session, e.g. right after scanning the RFID card at the station."""
    with _http_errors():
        session = await get_current_member_session(user_id=current_user["user_id"])

    return await _session_response(session, current_user, None)


@router.get(
    "/charging/{session_id}",
    response_model=MemberSessionResponse,
)
async def get_charging_session(
    session_id: str,
    current_user: CurrentMember,
):
    with _http_errors():
        session = await get_member_session(
            user_id=current_user["user_id"],
            session_id=session_id,
        )

    transaction = await get_transaction_for_session(session_id)
    return await _session_response(session, current_user, transaction)


@router.get(
    "/wallet",
    response_model=MemberWalletResponse,
)
async def get_wallet(current_user: CurrentMember):
    with _http_errors():
        return await get_member_wallet(user_id=current_user["user_id"])


@router.post(
    "/topup",
    response_model=DemoTopupResponse,
)
async def topup(
    payload: DemoTopupRequest,
    current_user: CurrentMember,
):
    with _http_errors():
        transaction = await demo_topup(
            user_id=current_user["user_id"],
            amount_vnd=payload.amount_vnd,
        )
    await events.topup_succeeded(current_user["user_id"], transaction)
    return transaction


@router.get(
    "/transactions",
    response_model=list[MemberTransactionResponse],
)
async def get_transactions(
    current_user: CurrentMember,
    limit: int = Limit,
    offset: int = Offset,
):
    return await list_member_transactions(
        user_id=current_user["user_id"],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=MemberTransactionResponse,
)
async def get_transaction_detail(
    transaction_id: str,
    current_user: CurrentMember,
):
    with _http_errors():
        return await get_member_transaction(
            user_id=current_user["user_id"],
            transaction_id=transaction_id,
        )


@router.get(
    "/sessions",
    response_model=list[MemberSessionHistoryResponse],
)
async def get_session_history(
    current_user: CurrentMember,
    limit: int = Limit,
    offset: int = Offset,
):
    return await list_member_sessions(
        user_id=current_user["user_id"],
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Favourite stations
# ---------------------------------------------------------------------------


@router.get(
    "/favorite-stations",
    response_model=list[FavoriteStationResponse],
)
async def get_favorite_stations(current_user: CurrentMember):
    return await list_favorites(user_id=current_user["user_id"])


@router.put(
    "/favorite-stations/{station_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def put_favorite_station(station_id: str, current_user: CurrentMember):
    with _http_errors():
        await add_favorite(user_id=current_user["user_id"], station_id=station_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/favorite-stations/{station_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_favorite_station(station_id: str, current_user: CurrentMember):
    await remove_favorite(user_id=current_user["user_id"], station_id=station_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# My RFID cards: lock / unlock / report lost
# ---------------------------------------------------------------------------


@router.get(
    "/rfid-cards",
    response_model=list[MyCardResponse],
)
async def get_my_cards(current_user: CurrentMember):
    return await cards.list_my_cards(user_id=current_user["user_id"])


@router.post(
    "/rfid-cards/{card_id}/lock",
    response_model=MyCardResponse,
)
async def lock_my_card(card_id: str, current_user: CurrentMember):
    """Temporary lock; unlock it from the app."""
    with _http_errors():
        return await cards.lock_card(current_user, card_id)


@router.post(
    "/rfid-cards/{card_id}/unlock",
    response_model=MyCardResponse,
)
async def unlock_my_card(card_id: str, current_user: CurrentMember):
    with _http_errors():
        return await cards.unlock_card(current_user, card_id)


@router.post(
    "/rfid-cards/{card_id}/report-lost",
    response_model=MyCardResponse,
)
async def report_my_card_lost(card_id: str, current_user: CurrentMember):
    """Permanent: the card is disabled for good and the desk issues a new one."""
    with _http_errors():
        return await cards.report_lost(current_user, card_id)


# ---------------------------------------------------------------------------
# Support requests
# ---------------------------------------------------------------------------


@router.post(
    "/support-tickets",
    response_model=MemberSupportTicket,
    status_code=status.HTTP_201_CREATED,
)
async def create_support_ticket(request: SupportTicketCreate, current_user: CurrentMember):
    """Ask for help at a station; technicians are notified with the station's state."""
    with _http_errors():
        return await support.create_ticket(
            current_user,
            station_id=request.station_id,
            category=request.category,
            description=request.description,
        )


@router.get(
    "/support-tickets",
    response_model=MemberSupportTicketPage,
)
async def get_support_tickets(
    current_user: CurrentMember,
    limit: int = Limit,
    offset: int = Offset,
):
    return await support.list_tickets(
        query={"user_id": current_user["user_id"]},
        limit=limit,
        offset=offset,
    )


@router.get(
    "/support-tickets/{ticket_id}",
    response_model=MemberSupportTicket,
)
async def get_support_ticket(ticket_id: str, current_user: CurrentMember):
    with _http_errors():
        return await support.get_member_ticket(current_user, ticket_id)


@router.post(
    "/support-tickets/{ticket_id}/cancel",
    response_model=MemberSupportTicket,
)
async def cancel_support_ticket(ticket_id: str, current_user: CurrentMember):
    with _http_errors():
        return await support.cancel_ticket(current_user, ticket_id)
