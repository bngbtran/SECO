"""Technician app: member support requests and the technician's own RFID cards."""

from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.dependencies import CurrentTechnician
from app.core.exceptions import BusinessRuleError, ConflictError, ResourceNotFoundError
from app.modules.cards import service as cards
from app.modules.cards.schemas import MyCardResponse
from app.modules.station.schemas import StationResponse
from app.modules.support import service as support
from app.modules.support.schemas import (
    SupportTicketResolve,
    TechnicianSupportTicket,
    TechnicianSupportTicketPage,
    TicketStatus,
)
from app.modules.technician import stations as tech_stations

router = APIRouter(prefix="/api/v1/technician", tags=["Technician"])


class TechnicianStation(StationResponse):
    open_error_count: int
    open_ticket_count: int
    temperature_c: float | None = None


class TechnicianStationDetail(BaseModel):
    station: StationResponse
    latest_telemetry: dict[str, Any] | None
    open_errors: list[dict[str, Any]]
    open_tickets: list[TechnicianSupportTicket]
    maintenance: list[dict[str, Any]]


class MaintenancePage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


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


@router.get("/support-tickets", response_model=TechnicianSupportTicketPage)
async def get_support_tickets(
    technician: CurrentTechnician,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    scope: Literal["all", "mine"] = "all",
    station_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The queue (status=OPEN), what I work on (scope=mine) or a station's history."""
    query: dict = {}
    if status_filter:
        query["status"] = status_filter
    if scope == "mine":
        query["assigned_to"] = technician["user_id"]
    if station_id:
        query["station_id"] = station_id
    return await support.list_tickets(query=query, limit=limit, offset=offset)


@router.get("/support-tickets/{ticket_id}", response_model=TechnicianSupportTicket)
async def get_support_ticket(ticket_id: str, technician: CurrentTechnician):
    with _http_errors():
        return await support.get_ticket(ticket_id)


@router.post("/support-tickets/{ticket_id}/accept", response_model=TechnicianSupportTicket)
async def accept_support_ticket(ticket_id: str, technician: CurrentTechnician):
    """Take an OPEN request; the member is told who is coming."""
    with _http_errors():
        return await support.accept_ticket(technician, ticket_id)


@router.post("/support-tickets/{ticket_id}/resolve", response_model=TechnicianSupportTicket)
async def resolve_support_ticket(
    ticket_id: str,
    request: SupportTicketResolve,
    technician: CurrentTechnician,
):
    with _http_errors():
        return await support.resolve_ticket(technician, ticket_id, request.resolution_note)


@router.get("/rfid-cards", response_model=list[MyCardResponse])
async def get_my_cards(technician: CurrentTechnician):
    return await cards.list_my_cards(user_id=technician["user_id"])


@router.post("/rfid-cards/{card_id}/lock", response_model=MyCardResponse)
async def lock_my_card(card_id: str, technician: CurrentTechnician):
    with _http_errors():
        return await cards.lock_card(technician, card_id)


@router.post("/rfid-cards/{card_id}/unlock", response_model=MyCardResponse)
async def unlock_my_card(card_id: str, technician: CurrentTechnician):
    with _http_errors():
        return await cards.unlock_card(technician, card_id)


@router.post("/rfid-cards/{card_id}/report-lost", response_model=MyCardResponse)
async def report_my_card_lost(card_id: str, technician: CurrentTechnician):
    with _http_errors():
        return await cards.report_lost(technician, card_id)


@router.get("/stations", response_model=list[TechnicianStation])
async def get_stations(technician: CurrentTechnician):
    """Stations needing a visit first (ERROR, MAINTENANCE, OFFLINE), with open errors / requests."""
    return await tech_stations.list_stations_for_technician()


@router.get("/stations/{station_id}", response_model=TechnicianStationDetail)
async def get_station_detail(station_id: str, technician: CurrentTechnician):
    """Live state, latest telemetry, open errors, member requests and recent maintenance."""
    with _http_errors():
        return await tech_stations.station_detail_for_technician(station_id)


@router.get("/maintenance", response_model=MaintenancePage)
async def get_my_maintenance(
    technician: CurrentTechnician,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """My maintenance history (entered / left maintenance at a station with my card)."""
    return await tech_stations.list_my_maintenance(
        technician_id=technician["user_id"], limit=limit, offset=offset
    )
