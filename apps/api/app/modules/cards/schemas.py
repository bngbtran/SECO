from datetime import datetime

from pydantic import BaseModel


class MyCardResponse(BaseModel):
    card_id: str
    uid_suffix: str | None
    label: str | None
    # ACTIVE | LOCKED | LOST | INACTIVE | PENDING
    status: str
    blocked_at: datetime | None
    created_at: datetime
    updated_at: datetime
