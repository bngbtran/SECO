from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaymentProvider(StrEnum):
    SANDBOX = "SANDBOX"
