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
    pending_max_budget_vnd: int | None = None


class MemberStartChargingRequest(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    station_id: str = Field(min_length=1, max_length=64)
    max_budget_vnd: int | None = Field(default=None, gt=0)


class MemberBudgetRequest(BaseModel):
    station_id: str = Field(min_length=1, max_length=64)
    max_budget_vnd: int = Field(gt=0)


class MemberBudgetResponse(BaseModel):
    user_id: str
    station_id: str
    max_budget_vnd: int
    balance_vnd: int
    created_at: datetime
    expires_at: datetime


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
    duration_seconds: int
    remaining_budget_vnd: int | None
    transaction_id: str | None
    transaction_amount_vnd: int | None
    transaction_status: str | None
    balance_after_vnd: int | None = None

class MemberWalletResponse(BaseModel):
    user_id: str
    balance_vnd: int
    held_balance_vnd: int
    available_balance_vnd: int


class MemberTransactionResponse(BaseModel):
    transaction_id: str
    type: str
    session_id: str | None
    amount_vnd: int
    balance_before_vnd: int | None
    balance_after_vnd: int | None
    status: str
    created_at: datetime

class MemberSessionHistoryResponse(BaseModel):
    session_id: str
    station_id: str
    station_name: str | None = None
    user_id: str
    status: str
    max_budget_vnd: int | None
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    started_at: datetime
    ended_at: datetime | None
    stop_reason: str | None
    duration_seconds: int
