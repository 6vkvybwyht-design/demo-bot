"""Нагадування клієнтам за 24 і за 2 години до візиту.

Задача запускається щохвилини й шукає записи, яким час нагадати. Прапорець
reminded_* фіксується в базі перед відправкою, тож після перезапуску бота
нагадування не дублюються.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from aiogram import Bot, html
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot import texts
from bot.config import BusinessConfig, Reminders
from bot.db.models import Booking, BookingStatus, utcnow
from bot.keyboards.booking import reminder_kb
from bot.services.booking import can_client_change
from bot.services.formatting import booking_summary, fmt_when

logger = logging.getLogger(__name__)

BEFORE_24H = timedelta(hours=24)
BEFORE_2H = timedelta(hours=2)


class ReminderKind(Enum):
    BEFORE_24H = "24h"
    BEFORE_2H = "2h"


@dataclass(frozen=True)
class Decision:
    send: ReminderKind | None  # яке нагадування надіслати (None — жодного)
    mark_24h: bool  # позначити reminded_24h
    mark_2h: bool  # позначити reminded_2h


def decide(booking: Booking, now: datetime, config: Reminders) -> Decision:
    """Що робити із записом зараз. Надсилаємо щонайбільше одне, актуальне нагадування.

    - візит уже почався — нічого;
    - до візиту ≤ 2 год: лише нагадування за 2 год; пропущене «за 24 год»
      (бот був вимкнений) позначаємо без відправки;
    - до візиту ≤ 24 год: нагадування за 24 год.
    """
    left = booking.start_dt - now
    if left <= timedelta(0):
        return Decision(None, False, False)

    if left <= BEFORE_2H:
        stale_24h = not booking.reminded_24h
        if not booking.reminded_2h:
            send = ReminderKind.BEFORE_2H if config.before_2h else None
            return Decision(send, mark_24h=stale_24h, mark_2h=True)
        return Decision(None, mark_24h=stale_24h, mark_2h=False)

    if left <= BEFORE_24H and not booking.reminded_24h:
        send = ReminderKind.BEFORE_24H if config.before_24h else None
        return Decision(send, mark_24h=True, mark_2h=False)
    return Decision(None, False, False)


async def pending_bookings(session: AsyncSession, now: datetime) -> list[Booking]:
    """Підтверджені записи в найближчі 24 год, що ще не почалися й мають невідправлені нагадування."""
    result = await session.scalars(
        select(Booking)
        .where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_dt > now,
            Booking.start_dt <= now + BEFORE_24H,
            or_(Booking.reminded_24h.is_(False), Booking.reminded_2h.is_(False)),
        )
        .options(
            selectinload(Booking.client), selectinload(Booking.master), selectinload(Booking.service)
        )
        .order_by(Booking.start_dt)
    )
    return list(result.all())


def reminder_text(booking: Booking, business: BusinessConfig, *, can_cancel: bool) -> str:
    """Текст нагадування. Якщо скасувати в боті вже не можна — додаємо телефон."""
    tz = business.booking.tz
    text = texts.REMINDER.format(
        when=fmt_when(booking.start_dt, tz),
        summary=booking_summary(
            service=booking.service, master_name=booking.master.name, start=booking.start_dt, tz=tz
        ),
        address=html.quote(business.business.address),
    )
    if not can_cancel:
        text += texts.REMINDER_CANCEL_DEADLINE.format(phone=html.quote(business.business.phone))
    return text


async def send_due_reminders(
    bot: Bot, session_factory: async_sessionmaker, business: BusinessConfig, now: datetime | None = None
) -> int:
    """Надсилає всі належні нагадування. Повертає кількість успішно надісланих."""
    now = now or utcnow()
    sent = 0
    async with session_factory() as session:
        for booking in await pending_bookings(session, now):
            decision = decide(booking, now, business.reminders)
            if decision.mark_24h:
                booking.reminded_24h = True
            if decision.mark_2h:
                booking.reminded_2h = True
            # Спершу фіксуємо прапорець, потім надсилаємо: краще втратити одне
            # нагадування через збій мережі, ніж надіслати його двічі
            await session.commit()
            if decision.send is None:
                continue
            can_cancel = can_client_change(booking, business.booking, now)
            try:
                await bot.send_message(
                    booking.client.telegram_id,
                    reminder_text(booking, business, can_cancel=can_cancel),
                    reply_markup=reminder_kb(booking.id, can_cancel=can_cancel),
                )
                sent += 1
            except TelegramAPIError as exc:
                logger.warning(
                    "Не вдалося надіслати нагадування (%s) за записом %s: %s",
                    decision.send.value, booking.id, exc,
                )
    return sent
