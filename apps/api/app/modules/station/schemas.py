from datetime import datetime

from pydantic import BaseModel


class StationResponse(BaseModel):
    station_id: str
    name: str
    status: str
    is_online: bool
    connector_lock: str
    current_session_id: str | None
    last_heartbeat_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
