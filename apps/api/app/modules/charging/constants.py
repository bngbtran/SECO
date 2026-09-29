from enum import StrEnum


class SessionType(StrEnum):
    MEMBER = "MEMBER"
    GUEST = "GUEST"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WAITING_PAYMENT = "WAITING_PAYMENT"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class StopReason(StrEnum):
    UNPLUG = "UNPLUG"
    MAX_BUDGET = "MAX_BUDGET"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    TECHNICIAN = "TECHNICIAN"
    OVER_TEMPERATURE = "OVER_TEMPERATURE"
    E_STOP = "E_STOP"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class StationStatus(StrEnum):
    READY = "READY"
    CHARGING = "CHARGING"
    ERROR = "ERROR"
    MAINTENANCE = "MAINTENANCE"


class ConnectorLock(StrEnum):
    UNLOCKED = "UNLOCKED"
    LOCKED = "LOCKED"
    UNKNOWN = "UNKNOWN"
