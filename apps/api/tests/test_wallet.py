import pytest

from app.core.exceptions import BusinessRuleError
from app.modules.billing.wallet import (
    available_balance_vnd,
    validate_hold_amount,
)


def test_available_balance():
    assert available_balance_vnd(200_000, 0) == 200_000
    assert available_balance_vnd(200_000, 50_000) == 150_000


def test_available_balance_rejects_invalid_hold():
    with pytest.raises(BusinessRuleError, match="held balance cannot exceed balance"):
        available_balance_vnd(100_000, 100_001)


def test_validate_hold_amount():
    assert validate_hold_amount(200_000, 50_000) == 50_000


def test_validate_hold_amount_rejects_exceeding_balance():
    with pytest.raises(BusinessRuleError, match="cannot exceed balance"):
        validate_hold_amount(100_000, 100_001)


def test_validate_hold_amount_rejects_zero():
    with pytest.raises(BusinessRuleError, match="must be > 0"):
        validate_hold_amount(100_000, 0)
