from fastapi import APIRouter, HTTPException, status

from app.modules.pricing.service import get_current_price
from app.modules.station.schemas import StationResponse
from app.modules.station.service import get_station, list_stations

router = APIRouter(prefix="/api/v1/stations", tags=["Stations"])


@router.get("", response_model=list[StationResponse])
async def stations():
    price = await get_current_price()
    return [{**station, "price_per_kwh_vnd": price} for station in await list_stations()]


@router.get("/{station_id}", response_model=StationResponse)
async def station(station_id: str):
    result = await get_station(station_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found",
        )
    return {**result, "price_per_kwh_vnd": await get_current_price()}
