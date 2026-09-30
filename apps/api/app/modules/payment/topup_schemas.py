from datetime import datetime

from pydantic import BaseModel, Field


class DemoTopupRequest(BaseModel):
    amount_vnd: int = Field(gt=0)


class DemoTopupResponse(BaseModel):
    transaction_id: str
    user_id: str
    amount_vnd: int
    balance_before_vnd: int
    balance_after_vnd: int
    status: str
    created_at: datetime