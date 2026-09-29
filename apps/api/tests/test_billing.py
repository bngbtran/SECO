from decimal import Decimal

from app.modules.billing.service import (
    cost_for_energy,
    effective_budget,
    energy_for_seconds,
    tick_values,
)


def test_energy_for_30_seconds():
    assert energy_for_seconds(30) == Decimal("0.00125")


def test_cost_for_30_seconds():
    assert cost_for_energy(Decimal("0.00125"), 4000) == 5


def test_tick_values():
    energy, cost = tick_values()
    assert energy == Decimal("0.00125")
    assert cost == 5


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
