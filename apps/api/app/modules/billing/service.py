from decimal import Decimal, ROUND_HALF_UP

from app.core.config import get_settings
from app.modules.billing.constants import (
    DEFAULT_POWER_KW,
    DEFAULT_PRICE_PER_KWH_VND,
    DEFAULT_TICK_SECONDS,
)


settings = get_settings()


def energy_for_seconds(
    seconds: int,
    power_kw: Decimal = DEFAULT_POWER_KW,
) -> Decimal:
    if seconds < 0:
        raise ValueError("seconds must be >= 0")
    return power_kw * Decimal(seconds) / Decimal(3600)


def cost_for_energy(
    energy_kwh: Decimal,
    price_per_kwh_vnd: int = DEFAULT_PRICE_PER_KWH_VND,
) -> int:
    if energy_kwh < 0:
        raise ValueError("energy_kwh must be >= 0")
    if price_per_kwh_vnd < 0:
        raise ValueError("price_per_kwh_vnd must be >= 0")
    value = energy_kwh * Decimal(price_per_kwh_vnd)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tick_values() -> tuple[Decimal, int]:
    energy = energy_for_seconds(
        settings.energy_tick_seconds,
        settings.energy_power_kw_decimal,
    )
    cost = cost_for_energy(energy, settings.price_per_kwh_vnd)
    return energy, cost


def effective_budget(
    balance_vnd: int,
    max_budget_vnd: int | None,
) -> int:
    if balance_vnd < 0:
        raise ValueError("balance must be >= 0")
    if max_budget_vnd is None:
        return balance_vnd
    if max_budget_vnd < 0:
        raise ValueError("max_budget_vnd must be >= 0")
    if max_budget_vnd > balance_vnd:
        raise ValueError("max_budget_vnd cannot exceed balance_vnd")
    return max_budget_vnd
