import pytest

from app.core.exceptions import BusinessRuleError
from app.modules.charging.service import ensure_tick_within_budget


def test_tick_within_budget():
    assert ensure_tick_within_budget(0, 5, 100) == 5
    assert ensure_tick_within_budget(95, 5, 100) == 100


def test_tick_exceeding_budget_raises():
    with pytest.raises(BusinessRuleError, match="exceed max budget"):
        ensure_tick_within_budget(5, 5, 7)


def test_none_budget_has_no_limit():
    assert ensure_tick_within_budget(100, 5, None) == 105


def test_negative_tick_values_raise():
    with pytest.raises(ValueError):
        ensure_tick_within_budget(0, -1, 100)
