"""Конфігурація: секрети з .env (pydantic-settings) і налаштування бізнесу з YAML."""

import json
import re
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = BASE_DIR / "config" / "profiles"
DEFAULT_PROFILE = "barber"
DEFAULT_BUSINESS_CONFIG = PROFILES_DIR / f"{DEFAULT_PROFILE}.yaml"


class BaseAppSettings(BaseSettings):
    """Параметри середовища без токена бота: потрібні міграціям і seed."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    demo_mode: bool = False
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    # Профіль бізнесу: config/profiles/<назва>.yaml (дані, тексти, демо-дані)
    business_profile: str = DEFAULT_PROFILE
    # Щоденний бекап SQLite: куди складати й скільки останніх копій зберігати
    backup_dir: Path = Path("data/backups")
    backup_keep: int = Field(default=7, ge=1)
    # Файл логу (з ротацією). Порожньо — лише в консоль (як у Docker)
    log_file: Path | None = None

    @field_validator("business_profile", mode="before")
    @classmethod
    def _empty_profile(cls, value: Any) -> Any:
        # «BUSINESS_PROFILE=» у .env — профіль за замовчуванням
        return DEFAULT_PROFILE if isinstance(value, str) and not value.strip() else value

    @field_validator("business_profile")
    @classmethod
    def _check_profile_name(cls, value: str) -> str:
        # Лише ім'я файлу в config/profiles — без шляхів на кшталт «../»
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError(
                f"BUSINESS_PROFILE: «{value}» — латинські малі літери, цифри, «-» або «_»"
            )
        return value

    @property
    def business_config_path(self) -> Path:
        return profile_path(self.business_profile)

    @field_validator("log_file", mode="before")
    @classmethod
    def _empty_log_file(cls, value: Any) -> Any:
        # «LOG_FILE=» у .env означає «без файлу», а не поточну папку
        return None if isinstance(value, str) and not value.strip() else value

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


def _time_in_quotes(value: Any) -> Any:
    # YAML читає «10:00» без лапок як число 600 (шістдесяткова система),
    # а pydantic перетворив би його на 00:10. Тому вимагаємо лапки.
    if isinstance(value, int):
        raise ValueError('час у профілі пишіть у лапках, наприклад "10:00"')
    return value


YamlTime = Annotated[time, BeforeValidator(_time_in_quotes)]


class DemoService(BaseModel):
    key: str  # коротка назва для посилань у профілі (майстри, демо-записи)
    name: str
    duration_min: int = Field(gt=0)
    price: int = Field(ge=0)


class DemoMaster(BaseModel):
    name: str
    services: list[str]  # ключі послуг


class DemoSchedule(BaseModel):
    weekdays: list[int]  # 0 — понеділок
    start: YamlTime
    end: YamlTime
    break_start: YamlTime | None = None
    break_end: YamlTime | None = None

    @model_validator(mode="after")
    def _check(self) -> "DemoSchedule":
        if not all(0 <= wd <= 6 for wd in self.weekdays):
            raise ValueError("weekdays: дні тижня від 0 (Пн) до 6 (Нд)")
        if self.start >= self.end:
            raise ValueError("графік: початок має бути раніше за кінець")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("перерва: вкажіть і break_start, і break_end")
        if self.break_start is not None and not (self.start < self.break_start < self.break_end < self.end):
            raise ValueError("перерва має бути всередині робочого дня")
        return self


class DemoClient(BaseModel):
    name: str
    phone: str


class DemoBooking(BaseModel):
    time: YamlTime
    service: str  # ключ послуги


class DemoTimeOff(BaseModel):
    master: str  # ім'я майстра
    day_offset: int = Field(ge=0)  # через скільки днів від сьогодні
    start: YamlTime
    end: YamlTime
    reason: str | None = None


class DemoData(BaseModel):
    """Тестовий бізнес для демо-режиму (seed і нічне скидання)."""

    services: list[DemoService]
    masters: list[DemoMaster]
    schedule: DemoSchedule
    clients: list[DemoClient]
    # Шаблони зайнятих слотів; день і майстер беруть шаблони по колу
    booking_patterns: list[list[DemoBooking]]
    booking_days: int = Field(default=7, gt=0)
    time_off: list[DemoTimeOff] = Field(default_factory=list)
    # Чим замінити послугу шаблону, якщо майстер її не виконує (за замовчуванням —
    # найдовша з його послуг, що не довша за початкову, щоб запис вмістився)
    fallback_service: str | None = None

    @model_validator(mode="after")
    def _check_references(self) -> "DemoData":
        keys = [s.key for s in self.services]
        if len(set(keys)) != len(keys):
            raise ValueError("demo.services: ключі послуг мають бути унікальні")
        used = {k for m in self.masters for k in m.services}
        used |= {b.service for pattern in self.booking_patterns for b in pattern}
        if self.fallback_service:
            used.add(self.fallback_service)
        if unknown := used - set(keys):
            raise ValueError(f"demo: невідомі ключі послуг: {', '.join(sorted(unknown))}")
        names = {m.name for m in self.masters}
        if unknown := {t.master for t in self.time_off} - names:
            raise ValueError(f"demo.time_off: невідомі майстри: {', '.join(sorted(unknown))}")
        if not self.clients:
            raise ValueError("demo.clients: потрібен хоча б один клієнт")
        return self

    def service(self, key: str) -> DemoService:
        return next(s for s in self.services if s.key == key)


class BusinessConfig(BaseModel):
    business: BusinessInfo
    booking: BookingRules = Field(default_factory=BookingRules)
    reminders: Reminders = Field(default_factory=Reminders)
    # Перевизначення текстів із bot/texts.py: {НАЗВА_КОНСТАНТИ: текст}
    texts: dict[str, str] = Field(default_factory=dict)
    demo: DemoData | None = None


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def profile_path(name: str) -> Path:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Профіль «{name}» не знайдено ({path}). Доступні: {', '.join(list_profiles())}"
        )
    return path


def load_business_config(path: Path | str = DEFAULT_BUSINESS_CONFIG) -> BusinessConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return BusinessConfig.model_validate(data)


@lru_cache
def get_settings() -> Settings:
    return Settings()
