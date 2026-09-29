from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class SessionSummary(BaseModel):
    session_id: str
    station_id: str
    type: str
    user_id: str | None
    status: str
    max_budget_vnd: int | None
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    stop_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    connector_locked: bool
