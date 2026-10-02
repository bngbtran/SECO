from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated

from fastapi import HTTPException, Query, status

from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    FeatureDisabledError,
    ResourceNotFoundError,
)
from app.db.mongodb import get_collection

users = get_collection("users")
stations = get_collection("charging_stations")

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@contextmanager
def http_errors():
    try:
        yield
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FeatureDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def page(items: list, total: int, limit: int, offset: int) -> dict:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def time_range(field: str, date_from: datetime | None, date_to: datetime | None) -> dict:
    if not date_from and not date_to:
        return {}
    bounds = {}
    if date_from:
        bounds["$gte"] = date_from
    if date_to:
        bounds["$lt"] = date_to
    return {field: bounds}


async def user_names(user_ids: Iterable[str | None]) -> dict[str, str]:
    ids = list({uid for uid in user_ids if uid})
    if not ids:
        return {}
    return {
        u["user_id"]: u["name"]
        async for u in users.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "name": 1})
    }


async def station_names(station_ids: Iterable[str | None]) -> dict[str, str]:
    ids = list({sid for sid in station_ids if sid})
    if not ids:
        return {}
    return {
        s["station_id"]: s["name"]
        async for s in stations.find(
            {"station_id": {"$in": ids}},
            {"_id": 0, "station_id": 1, "name": 1},
        )
    }
