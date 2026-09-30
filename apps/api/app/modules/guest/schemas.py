from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class GuestPaymentResponse(BaseModel):
    payment_id: str
    amount_vnd: int
    status: str
    provider: str
    qr_payload: str
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None


class GuestSessionResponse(BaseModel):
    """Live status while charging (UC-G04) and the receipt afterwards (UC-G11)."""

    session_id: str
    station_id: str
    station_name: str | None
    status: str
    max_budget_vnd: int | None
    remaining_budget_vnd: int | None
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int
    stop_reason: str | None
    connector_locked: bool
    payment: GuestPaymentResponse | None
    transaction_id: str | None


class GuestPaymentPageResponse(GuestPaymentResponse):
    """Data for the sandbox payment page opened from the QR (UC-G08)."""

    session_id: str
    station_id: str
    station_name: str | None
    energy_kwh: Decimal
    stop_reason: str | None
    duration_seconds: int
