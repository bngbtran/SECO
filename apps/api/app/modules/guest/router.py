from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, status

from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.charging.constants import SessionType
from app.modules.guest.realtime import announce_paid, announce_payment_qr, announce_payment_status
from app.modules.guest.schemas import (
    GuestPaymentPageResponse,
    GuestPaymentResponse,
    GuestSessionResponse,
)
from app.modules.guest.service import (
    cancel_payment,
    get_guest_session_view,
    get_payment_view,
    pay_payment,
    request_new_payment,
)

# Guests have no account: session_id / payment_id are unguessable UUIDs that the
# station screen and the QR hand out, so they act as the access key.
router = APIRouter(
    prefix="/api/v1/guest",
    tags=["Guest"],
)

stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")


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


@router.get(
    "/stations/{station_id}/session",
    response_model=GuestSessionResponse,
)
async def get_station_guest_session(station_id: str):
    """The guest session currently bound to a station (station screen / web)."""
    station = await stations.find_one(
        {"station_id": station_id},
        {"_id": 0, "current_session_id": 1},
    )
    if not station:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    session_id = station.get("current_session_id")
    is_guest = session_id and await sessions.find_one(
        {"session_id": session_id, "type": SessionType.GUEST.value},
        {"_id": 1},
    )
    if not is_guest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No guest session at this station",
        )

    return await get_guest_session_view(session_id)


@router.get(
    "/sessions/{session_id}",
    response_model=GuestSessionResponse,
)
async def get_guest_session(session_id: str):
    with _http_errors():
        return await get_guest_session_view(session_id)


@router.post(
    "/sessions/{session_id}/payments",
    response_model=GuestPaymentResponse,
)
async def create_guest_payment(session_id: str):
    """Get the open QR for a session waiting for payment, or a new one if it expired."""
    with _http_errors():
        payment = await request_new_payment(session_id)

    await announce_payment_qr(payment)
    return payment


@router.get(
    "/payments/{payment_id}",
    response_model=GuestPaymentPageResponse,
)
async def get_guest_payment(payment_id: str):
    with _http_errors():
        return await get_payment_view(payment_id)


@router.post(
    "/payments/{payment_id}/pay",
    response_model=GuestSessionResponse,
)
async def pay_guest_payment(payment_id: str):
    """Sandbox provider: confirm the payment, unlock the connector and return the receipt."""
    with _http_errors():
        result = await pay_payment(payment_id)

    await announce_paid(result)
    return await get_guest_session_view(result["session"]["session_id"])


@router.post(
    "/payments/{payment_id}/cancel",
    response_model=GuestPaymentResponse,
)
async def cancel_guest_payment(payment_id: str):
    with _http_errors():
        payment = await cancel_payment(payment_id)

    if payment["status"] == "CANCELLED":
        await announce_payment_status(payment)
    return payment
