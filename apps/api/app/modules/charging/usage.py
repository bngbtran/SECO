from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import get_settings
from app.core.mongo_types import from_decimal128
from app.modules.billing.service import cost_for_energy, energy_for_seconds
from app.modules.charging.service import set_session_usage

settings = get_settings()


def now_utc() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    # PyMongo returns naive UTC datetimes unless tz_aware is enabled.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def serialize_session(session: dict) -> dict:
    result = dict(session)
    for key in ("energy_kwh", "power_kw"):
        if result.get(key) is not None:
            result[key] = from_decimal128(result[key])
    return result


async def refresh_session_usage(session: dict) -> tuple[dict, bool]:
    """Recompute usage of an ACTIVE session (member or guest) from elapsed time.

    Energy is simulated (DD 5): 0.15 kW every 30 s tick, so usage depends only on
    how many whole ticks have passed since the session started. This keeps the
    update idempotent no matter how often telemetry or the ticker call it.
    Returns the (possibly updated) session and whether the next tick would
    exceed the budget.
    """
    tick_seconds = settings.energy_tick_seconds
    price = int(session.get("price_per_kwh_vnd", settings.price_per_kwh_vnd))
    power_kw = from_decimal128(session.get("power_kw", settings.energy_power_kw_decimal))
    # The session's own snapshot price, not today's tariff.
    tick_cost_vnd = cost_for_energy(energy_for_seconds(tick_seconds, power_kw), price)

    elapsed = (now_utc() - _as_utc(session["started_at"])).total_seconds()
    ticks = max(int(elapsed // tick_seconds), 0)

    budget = session.get("max_budget_vnd")
    if budget is not None and tick_cost_vnd > 0:
        ticks = min(ticks, int(budget) // tick_cost_vnd)

    energy_kwh = energy_for_seconds(ticks * tick_seconds, power_kw)
    cost_vnd = cost_for_energy(energy_kwh, price)

    current_energy = from_decimal128(session.get("energy_kwh", Decimal(0)))
    if energy_kwh > current_energy:
        updated = await set_session_usage(
            session_id=session["session_id"],
            energy_kwh=energy_kwh,
            cost_vnd=cost_vnd,
        )
        session = updated or session

    budget_exhausted = budget is not None and cost_vnd + tick_cost_vnd > int(budget)
    return session, budget_exhausted


def session_live_fields(session: dict) -> dict:
    """Derived values the member app shows while charging and in the result screen."""
    started_at = _as_utc(session["started_at"])
    ended_at = _as_utc(session["ended_at"]) if session.get("ended_at") else now_utc()
    budget = session.get("max_budget_vnd")
    cost_vnd = int(session.get("cost_vnd", 0))

    return {
        "duration_seconds": max(int((ended_at - started_at).total_seconds()), 0),
        "remaining_budget_vnd": max(int(budget) - cost_vnd, 0) if budget is not None else None,
    }
