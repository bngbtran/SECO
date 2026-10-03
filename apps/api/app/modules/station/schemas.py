from datetime import datetime

from pydantic import BaseModel


class StationResponse(BaseModel):
    station_id: str
    name: str
    status: str
    availability: str
    is_online: bool
    connector_lock: str
    current_session_id: str | None
    last_heartbeat_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    # No hardware behind it (map demo); a technician's "fixed" in the app puts it back to READY.
    is_simulated: bool = False
    # Tariff in force (the same for every station); a session keeps its own snapshot.
    price_per_kwh_vnd: int | None = None


class FavoriteStationResponse(StationResponse):
    favorited_at: datetime
