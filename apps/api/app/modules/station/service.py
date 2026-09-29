from app.db.mongodb import get_collection


stations = get_collection("charging_stations")


async def list_stations() -> list[dict]:
    cursor = stations.find({}, {"_id": 0}).sort("station_id", 1)
    return await cursor.to_list(length=500)


async def get_station(station_id: str) -> dict | None:
    return await stations.find_one({"station_id": station_id}, {"_id": 0})
