from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_env: str = "development"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    mongo_uri: str = "mongodb://localhost:27017/?replicaSet=rs0"
    mongo_database: str = "seco"

    jwt_secret: str = "seco-dev-secret-change-this-before-production-2026"
    jwt_expire_minutes: int = 1440

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = False
    mqtt_client_id: str = "seco-api"
    # API instances sharing a broker form one shared-subscription group for
    # station requests, so each request is handled (and answered) once.
    # Empty disables it (every instance handles every request).
    mqtt_share_group: str = "seco-api"

    # Admin desk RFID reader used to assign cards to members (simulator/admin).
    admin_reader_id: str = "READER-01"
    card_registration_timeout_seconds: int = 90

    web_origin: str = "http://localhost:5173"

    energy_power_kw: str = "0.15"
    energy_tick_seconds: int = 30
    price_per_kwh_vnd: int = 4000
    session_ticker_interval_seconds: int = 5

    demo_topup_enabled: bool = False
    demo_topup_max_vnd: int = 10_000_000

    guest_max_budget_vnd: int = 1_000_000
    guest_payment_ttl_seconds: int = 300
    # The QR encodes <base>/<payment_id>; the web app serves the sandbox payment page there.
    guest_payment_url_base: str = "http://localhost:5173/pay"

    # Forgot password: a temporary password stands in for the old one.
    password_reset_ttl_minutes: int = 10
    # Longer when an admin hands it over (in person, by phone).
    admin_temp_password_ttl_minutes: int = 60
    password_reset_max_attempts: int = 5

    # Member notification after a session when the balance drops below this.
    low_balance_threshold_vnd: int = 20_000

    model_config = SettingsConfigDict(
        env_file=API_ROOT / ".env",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def energy_power_kw_decimal(self):
        from decimal import Decimal

        return Decimal(self.energy_power_kw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
