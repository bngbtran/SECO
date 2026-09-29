from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RFIDVerifyRequest(BaseModel):
    uid: str = Field(min_length=1, max_length=64)


class MemberIdentityResponse(BaseModel):
    user_id: str
    name: str
    role: str
    balance_vnd: int
    card_id: str


class MemberStartChargingRequest(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    station_id: str = Field(min_length=1, max_length=64)
    max_budget_vnd: int | None = Field(default=None, ge=0)


class MemberChargingResponse(BaseModel):
    session_id: str
    station_id: str
    user_id: str
    user_name: str
    role: str
    balance_vnd: int
    max_budget_vnd: int
    status: str
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    started_at: datetime

class MemberSessionResponse(BaseModel):
    session_id: str
    station_id: str
    user_id: str
    user_name: str
    role: str
    balance_vnd: int
    max_budget_vnd: int | None
    status: str
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    started_at: datetime
    ended_at: datetime | None
    stop_reason: str | None
    transaction_id: str | None
    transaction_amount_vnd: int | None
    transaction_status: str | None