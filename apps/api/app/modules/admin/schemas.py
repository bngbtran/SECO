from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from app.modules.auth.schemas import UserMe

T = TypeVar("T")

Role = Literal["USER", "ADMIN", "TECHNICIAN"]


class Page(BaseModel, Generic[T]):  # noqa: UP046 - runtime is Python 3.11
    items: list[T]
    total: int
    limit: int
    offset: int


# --- Auth (UC-A01) -----------------------------------------------------------


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserMe


# --- Users & RFID cards (UC-A02) ---------------------------------------------


class AdminUser(BaseModel):
    user_id: str
    name: str
    email: str
    phone: str | None = None
    role: str
    balance_vnd: int
    is_active: bool
    card_count: int = 0
    created_at: datetime
    updated_at: datetime


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=4, max_length=255)
    role: Role = "USER"
    phone: str | None = Field(default=None, max_length=32)


class AdminUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    phone: str | None = Field(default=None, max_length=32)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=4, max_length=255)


class AdminCard(BaseModel):
    card_id: str
    uid: str | None
    user_id: str | None
    user_name: str | None = None
    label: str | None = None
    is_active: bool
    pending: bool
    # ACTIVE | INACTIVE | PENDING (waiting for a UID scan) | UNASSIGNED (no owner)
    status: str
    created_at: datetime
    updated_at: datetime


class AdminCardCreate(BaseModel):
    uid: str | None = Field(default=None, min_length=1, max_length=64)
    user_id: str | None = None
    label: str | None = Field(default=None, max_length=64)


class AdminCardUpdate(BaseModel):
    uid: str | None = Field(default=None, min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class AdminCardLink(BaseModel):
    user_id: str


class AdminVehicle(BaseModel):
    vehicle_id: str
    plate_number: str
    brand: str
    model: str
    battery_kwh: float | None = None
    is_active: bool


# --- Sessions, transactions, payments, holds (UC-A05..A07, A09) --------------


class AdminSession(BaseModel):
    session_id: str
    station_id: str
    station_name: str | None = None
    type: str
    user_id: str | None
    user_name: str | None = None
    status: str
    max_budget_vnd: int | None
    budget_mode: str | None = None
    remaining_budget_vnd: int | None = None
    energy_kwh: Decimal
    power_kw: Decimal
    price_per_kwh_vnd: int
    cost_vnd: int
    stop_reason: str | None
    connector_locked: bool
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int


class AdminTransaction(BaseModel):
    transaction_id: str
    type: str
    user_id: str | None
    user_name: str | None = None
    session_id: str | None
    payment_id: str | None = None
    payment_method: str | None = None
    amount_vnd: int
    balance_before_vnd: int | None = None
    balance_after_vnd: int | None = None
    status: str
    created_at: datetime


class AdminPayment(BaseModel):
    payment_id: str
    session_id: str
    station_id: str | None = None
    station_name: str | None = None
    amount_vnd: int
    status: str
    provider: str
    qr_payload: str
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None


class AdminWalletHold(BaseModel):
    wallet_hold_id: str
    user_id: str
    user_name: str | None = None
    session_id: str
    amount_vnd: int
    status: str
    created_at: datetime
    released_at: datetime | None = None


class AdminSessionDetail(AdminSession):
    transactions: list[AdminTransaction]
    payments: list[AdminPayment]


class AdminUserDetail(AdminUser):
    cards: list[AdminCard]
    vehicles: list[AdminVehicle]
    open_session: AdminSession | None
    recent_sessions: list[AdminSession]
    recent_transactions: list[AdminTransaction]


# --- Stations (UC-A03, A08, A14) ----------------------------------------------


class AdminStation(BaseModel):
    station_id: str
    name: str
    address: str | None = None
    status: str
    availability: str
    is_online: bool
    is_simulated: bool = False
    connector_lock: str
    current_session_id: str | None
    last_heartbeat_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    latest_telemetry: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class AdminStationCreate(BaseModel):
    station_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$")
    name: str = Field(min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_simulated: bool = False


class AdminStationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_simulated: bool | None = None


class AdminLiveStation(AdminStation):
    current_session: AdminSession | None = None


class AdminMaintenance(BaseModel):
    maintenance_id: str
    station_id: str
    station_name: str | None = None
    technician_id: str
    technician_name: str | None = None
    error_id: str | None = None
    status: str
    description: str
    started_at: datetime
    completed_at: datetime | None


class AdminErrorLog(BaseModel):
    error_id: str
    station_id: str
    station_name: str | None = None
    session_id: str | None = None
    error_type: str
    severity: str
    message: str
    details: dict[str, Any] | None = None
    occurred_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolved_by_name: str | None = None


class AdminStationDetail(AdminStation):
    current_session: AdminSession | None
    recent_sessions: list[AdminSession]
    open_errors: list[AdminErrorLog]
    maintenance: list[AdminMaintenance]


StationCommand = Literal["STOP", "MAINTENANCE", "READY", "RESET", "LOCK", "UNLOCK", "PING"]


class AdminStationCommandRequest(BaseModel):
    command: StationCommand
    note: str | None = Field(default=None, max_length=255)


class AdminStationCommandResponse(BaseModel):
    station_id: str
    command: str
    request_id: str
    # False when the MQTT broker is not connected; DB changes still applied.
    published: bool
    stopped_session: AdminSession | None = None
    station: AdminStation


# --- Logs (UC-A11..A13) --------------------------------------------------------


class AdminAuditLog(BaseModel):
    audit_id: str
    actor_user_id: str
    actor_name: str | None = None
    actor_role: str
    action: str
    target_type: str
    target_id: str
    metadata: dict[str, Any] = {}
    created_at: datetime


# --- Price (UC-A04) ------------------------------------------------------------


class AdminPrice(BaseModel):
    price_id: str
    price_per_kwh_vnd: int
    effective_from: datetime
    effective_to: datetime | None
    created_by: str | None = None
    created_by_name: str | None = None
    note: str | None = None
    # Only set in the response of POST /prices.
    previous_price_per_kwh_vnd: int | None = None


class AdminCurrentPrice(BaseModel):
    price_per_kwh_vnd: int
    # Cost of one simulated 30 s tick (0.00125 kWh) at this price.
    tick_cost_vnd: int
    effective_from: datetime | None


class AdminPriceUpdate(BaseModel):
    price_per_kwh_vnd: int
    note: str | None = Field(default=None, max_length=255)


# --- Dashboard (UC-A10) --------------------------------------------------------


class DashboardDay(BaseModel):
    date: str
    sessions: int
    energy_kwh: Decimal
    revenue_vnd: int


class AdminDashboard(BaseModel):
    generated_at: datetime
    stations_total: int
    stations_by_availability: dict[str, int]
    sessions_active: int
    sessions_waiting_payment: int
    sessions_completed_today: int
    energy_today_kwh: Decimal
    revenue_today_vnd: int
    members_total: int
    members_active: int
    open_errors: int
    maintenance_in_progress: int
    price_per_kwh_vnd: int
    last_7_days: list[DashboardDay]
    live_sessions: list[AdminSession]
    recent_errors: list[AdminErrorLog]
