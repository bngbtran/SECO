from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TicketCategory = Literal[
    "CANNOT_START",
    "CHARGING_INTERRUPTED",
    "CONNECTOR_STUCK",
    "PAYMENT",
    "CARD",
    "STATION_DAMAGED",
    "OTHER",
]
TicketStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED"]


class SupportTicketCreate(BaseModel):
    station_id: str = Field(min_length=1, max_length=64)
    category: TicketCategory
    description: str = Field(default="", max_length=1000)


class SupportTicketResolve(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=1000)


class MemberSupportTicket(BaseModel):
    ticket_id: str
    station_id: str
    station_name: str | None
    category: str
    category_label: str
    description: str
    status: str
    session_id: str | None
    assigned_name: str | None
    assigned_at: datetime | None
    resolution_note: str | None
    resolved_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TechnicianSupportTicket(MemberSupportTicket):
    user_id: str
    user_name: str | None
    # So the technician can call the member at the station.
    user_phone: str | None
    assigned_to: str | None
    # Station state when the member asked for help.
    station_snapshot: dict[str, Any]


class MemberSupportTicketPage(BaseModel):
    items: list[MemberSupportTicket]
    total: int
    limit: int
    offset: int


class TechnicianSupportTicketPage(BaseModel):
    items: list[TechnicianSupportTicket]
    total: int
    limit: int
    offset: int
