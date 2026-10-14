"""Конфігурація: секрети з .env (pydantic-settings) і налаштування бізнесу з YAML."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BUSINESS_CONFIG = BASE_DIR / "config" / "business.yaml"


class BaseAppSettings(BaseSettings):
    """Параметри середовища без токена бота: потрібні міграціям і seed."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    demo_mode: bool = False
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    business_config_path: Path = DEFAULT_BUSINESS_CONFIG

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: Any) -> Any:
        # Приймаємо як "1,2,3", так і JSON-список "[1, 2, 3]"
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                return json.loads(value)
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class Settings(BaseAppSettings):
    """Повні налаштування бота. Читаються з .env або змінних оточення."""

    bot_token: SecretStr


class BusinessInfo(BaseModel):
    name: str
    address: str
    phone: str
    map_url: str | None = None
    instagram: str | None = None


class BookingRules(BaseModel):
    timezone: str = "Europe/Kyiv"
    slot_step_min: int = Field(default=15, gt=0)
    horizon_days: int = Field(default=14, gt=0)
    min_lead_time_min: int = Field(default=60, ge=0)
    cancel_deadline_hours: int = Field(default=2, ge=0)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Невідомий часовий пояс: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Reminders(BaseModel):
    before_24h: bool = True
    before_2h: bool = True


class BusinessConfig(BaseModel):
    business: BusinessInfo
    booking: BookingRules = Field(default_factory=BookingRules)
    reminders: Reminders = Field(default_factory=Reminders)


def load_business_config(path: Path | str = DEFAULT_BUSINESS_CONFIG) -> BusinessConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return BusinessConfig.model_validate(data)


@lru_cache
def get_settings() -> Settings:
    return Settings()
