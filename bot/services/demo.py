"""Демо-режим: хто дивиться адмінку, маскування чужих даних, вигадані демо-клієнти."""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BusinessConfig, Settings
from bot.db.models import Client, Service

logger = logging.getLogger(__name__)

# Вигадані Telegram ID демо-клієнтів із seed (справжні ID таких не бувають у цьому діапазоні)
DEMO_CLIENT_TG_BASE = 900_000_000
DEMO_CLIENT_TG_RANGE = 1_000


def is_demo_client(telegram_id: int | None) -> bool:
    return telegram_id is not None and DEMO_CLIENT_TG_BASE < telegram_id <= DEMO_CLIENT_TG_BASE + DEMO_CLIENT_TG_RANGE


def mask_phone(phone: str | None) -> str | None:
    """«+380501234593» → «+380 50 *** ** 93». Інші формати — перші 4 і останні 2 символи."""
    if not phone:
        return phone
    if match := re.fullmatch(r"\+380(\d{2})\d{5}(\d{2})", phone):
        return f"+380 {match[1]} *** ** {match[2]}"
    return f"{phone[:4]}***{phone[-2:]}" if len(phone) > 6 else "***"


def mask_name(name: str) -> str:
    """«Олександр» → «Ол***»."""
    name = name.strip()
    visible = 2 if len(name) > 2 else 1
    return f"{name[:visible]}***"


@dataclass(frozen=True)
class Viewer:
    """Хто дивиться адмінку.

    Справжній власник (ADMIN_IDS) бачить усе. У демо-режимі будь-хто може відкрити
    адмінку як «демо-власник»: він бачить повністю лише власні записи та вигаданих
    демо-клієнтів, решту реальних людей — замасковано.
    """

    user_id: int
    real_admin: bool
    demo_mode: bool

    @property
    def is_demo_owner(self) -> bool:
        return self.demo_mode and not self.real_admin

    def sees_fully(self, client: Client) -> bool:
        return (
            not self.is_demo_owner
            or client.telegram_id == self.user_id
            or is_demo_client(client.telegram_id)
        )

    def client_name(self, client: Client) -> str:
        return client.name if self.sees_fully(client) else mask_name(client.name)

    def client_phone(self, client: Client) -> str | None:
        return client.phone if self.sees_fully(client) else mask_phone(client.phone)

    def may_notify(self, telegram_id: int | None) -> bool:
        """Демо-власник своїми діями може сповіщати лише самого себе."""
        return not self.is_demo_owner or telegram_id == self.user_id


# Справжній власник: без маскування й обмежень
FULL_ACCESS = Viewer(user_id=0, real_admin=True, demo_mode=False)


def viewer_for(user_id: int, settings: Settings) -> Viewer | None:
    """Глядач адмінки або None, якщо доступу немає."""
    real_admin = user_id in settings.admin_ids
    if not real_admin and not settings.demo_mode:
        return None
    return Viewer(user_id=user_id, real_admin=real_admin, demo_mode=settings.demo_mode)


async def reset_demo(session_factory: async_sessionmaker, business: BusinessConfig) -> None:
    """Повертає демо до початкового стану: записи, клієнти, майстри, послуги,
    графік і блокування — усе, що могли змінити відвідувачі."""
    from seed.demo_data import reset_demo_data  # дані демо-салону живуть у seed

    async with session_factory() as session, session.begin():
        summary = await reset_demo_data(session, business)
    logger.info("Демо-дані скинуто: %s", summary)


async def ensure_demo_data(session_factory: async_sessionmaker, business: BusinessConfig) -> None:
    """Під час старту в демо-режимі заповнює порожню базу."""
    async with session_factory() as session:
        has_services = await session.scalar(select(func.count()).select_from(Service))
    if not has_services:
        await reset_demo(session_factory, business)
