"""Сповіщення власнику, майстру й клієнту.

Помилка відправки (клієнт заблокував бота, чат не знайдено, мережа) не повинна
ламати основну дію: її логуємо й продовжуємо.
"""

import logging
from collections.abc import Iterable
from datetime import datetime

from aiogram import Bot, html
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db.repo import booking_details

from bot import texts
from bot.config import BusinessConfig
from bot.db.models import Booking
from bot.services.demo import FULL_ACCESS, Viewer, is_demo_client
from bot.services.formatting import fmt_when, summary_of

logger = logging.getLogger(__name__)


async def safe_send(bot: Bot, chat_id: int, text: str) -> bool:
    """Надсилає повідомлення. Повертає False, якщо не вдалося (і логує причину)."""
    if is_demo_client(chat_id):
        # Вигаданий демо-клієнт — такого чату не існує
        return False
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError as exc:
        logger.warning("Не вдалося надіслати сповіщення в чат %s: %s", chat_id, exc)
        return False
    return True


async def load_booking(session: AsyncSession, booking_id: int) -> Booking:
    """Запис з клієнтом, майстром і послугою — для тексту сповіщень."""
    return await session.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(*booking_details())
        .execution_options(populate_existing=True)
    )


def staff_recipients(
    booking: Booking,
    admin_ids: Iterable[int],
    exclude: int | None = None,
    *,
    demo_mode: bool = False,
    actor_id: int | None = None,
) -> list[int]:
    """Усі власники й майстер запису (якщо вказаний його Telegram ID), без дублів.

    У демо-режимі Telegram ID майстра може вписати будь-який відвідувач, тому
    майстру пишемо, лише якщо це сам відвідувач, що виконав дію.
    """
    master_id = booking.master.telegram_id
    if demo_mode and master_id != actor_id:
        master_id = None
    recipients = list(dict.fromkeys([*admin_ids, master_id]))
    return [chat_id for chat_id in recipients if chat_id is not None and chat_id != exclude]


def staff_details(booking: Booking, business: BusinessConfig, viewer: Viewer = FULL_ACCESS) -> str:
    summary = summary_of(booking, business)
    return texts.STAFF_DETAILS.format(
        summary=summary,
        client=html.quote(viewer.client_name(booking.client)),
        phone=html.quote(viewer.client_phone(booking.client) or texts.NO_PHONE),
    )


async def notify_staff(
    bot: Bot,
    session: AsyncSession,
    *,
    booking_id: int,
    template: str,
    admin_ids: Iterable[int],
    business: BusinessConfig,
    old_start: datetime | None = None,
    exclude: int | None = None,
    demo_mode: bool = False,
    actor_id: int | None = None,
) -> str:
    """Сповіщення власникам і майстру. `exclude` — хто сам виконав дію.

    Повертає текст сповіщення (у демо його показуємо й відвідувачу).
    """
    booking = await load_booking(session, booking_id)
    extra = {"old": fmt_when(old_start, business.booking.tz)} if old_start else {}
    text = template.format(details=staff_details(booking, business), **extra)
    for chat_id in staff_recipients(
        booking, admin_ids, exclude, demo_mode=demo_mode, actor_id=actor_id
    ):
        await safe_send(bot, chat_id, text)
    return text


async def notify_client_cancelled_by_admin(
    bot: Bot, session: AsyncSession, *, booking_id: int, business: BusinessConfig
) -> bool:
    booking = await load_booking(session, booking_id)
    summary = summary_of(booking, business)
    text = texts.CLIENT_CANCELLED_BY_ADMIN.format(
        summary=summary, phone=html.quote(business.business.phone)
    )
    return await safe_send(bot, booking.client.telegram_id, text)
