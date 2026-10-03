"""Member help requests / broken-station reports, seen from the web admin (read only).

Technicians handle them in the mobile app; the admin follows them next to the
station errors (Faults page) and the station itself.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentAdmin
from app.modules.admin.common import Limit, Offset, http_errors
from app.modules.support import service as support
from app.modules.support.schemas import (
    TechnicianSupportTicket,
    TechnicianSupportTicketPage,
    TicketStatus,
)

router = APIRouter(tags=["Admin · Support requests"])


@router.get("/support-tickets", response_model=TechnicianSupportTicketPage)
async def get_support_tickets(
    admin: CurrentAdmin,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    active: bool = False,
    station_id: str | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
):
    """active=true: OPEN + IN_PROGRESS (still to be handled)."""
    query: dict = {}
    if active:
        query["status"] = {"$in": list(support.OPEN_STATUSES)}
    elif status_filter:
        query["status"] = status_filter
    if station_id:
        query["station_id"] = station_id
    return await support.list_tickets(query=query, limit=limit, offset=offset)


@router.get("/support-tickets/{ticket_id}", response_model=TechnicianSupportTicket)
async def get_support_ticket(ticket_id: str, admin: CurrentAdmin):
    with http_errors():
        return await support.get_ticket(ticket_id)
