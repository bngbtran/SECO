from decimal import Decimal

from bson.decimal128 import Decimal128


def to_decimal128(value: Decimal | float | str) -> Decimal128:
    return Decimal128(str(value))


def from_decimal128(value: Decimal128 | Decimal | float | str) -> Decimal:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    return Decimal(str(value))
