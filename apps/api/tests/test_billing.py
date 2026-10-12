from decimal import Decimal

from app.modules.billing.service import (
    cost_for_energy,
    effective_budget,
    energy_for_seconds,
    tick_values,
)


def test_energy_for_30_seconds():
    assert energy_for_seconds(30) == Decimal("0.1") * 30 / 3600
    assert energy_for_seconds(3600) == Decimal("0.1")


def test_cost_for_30_seconds():
    assert cost_for_energy(Decimal("0.00125"), 4000) == 5
    # 0.1 kW for 30 s is 3.33 VND; the cost is rounded from the total energy.
    assert cost_for_energy(energy_for_seconds(30), 4000) == 3
    assert cost_for_energy(energy_for_seconds(90), 4000) == 10


def test_tick_values():
    energy, cost = tick_values()
    assert energy == Decimal("0.1") * 30 / 3600
    assert cost == 3


def test_budget_must_not_exceed_balance():
    assert effective_budget(100_000, None) == 100_000
    assert effective_budget(100_000, 50_000) == 50_000


def test_budget_exceed_balance_raises():
    try:
        effective_budget(50_000, 100_000)
    except ValueError as exc:
        assert "cannot exceed" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
