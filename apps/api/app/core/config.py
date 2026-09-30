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

    web_origin: str = "http://localhost:5173"

    energy_power_kw: str = "0.15"
    energy_tick_seconds: int = 30
    price_per_kwh_vnd: int = 4000
    session_ticker_interval_seconds: int = 5

    demo_topup_enabled: bool = False
    demo_topup_max_vnd: int = 10_000_000

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
