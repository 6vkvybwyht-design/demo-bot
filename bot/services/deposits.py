"""Аванс: автоскасування бронювань, за які не внесли аванс вчасно.

Задача запускається щохвилини (лише для профілів зі spa). Бронювання без авансу
тримає час `spa.deposit.hold_min` хвилин, потім скасовується, а клієнт і власник
отримують сповіщення.
"""

import logging
from collections.abc import Iterable
from datetime import datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import texts
from bot.config import BusinessConfig
from bot.db.models import utcnow
from bot.services import booking as booking_service
from bot.services import notifications

logger = logging.getLogger(__name__)


async def expire_unpaid_bookings(
    bot: Bot,
    session_factory: async_sessionmaker,
    business: BusinessConfig,
    admin_ids: Iterable[int] = (),
    demo_mode: bool = False,
    now: datetime | None = None,
) -> int:
    """Скасовує прострочені бронювання без авансу. Повертає їх кількість."""
    now = now or utcnow()
    admin_ids = list(admin_ids)
    async with session_factory() as session:
        expired = await booking_service.expire_unpaid(session, now)
        for booking in expired:
            logger.info("Бронювання %s скасовано: аванс не внесено", booking.id)
            await notifications.notify_client(
                bot, session, booking_id=booking.id, template=texts.DEPOSIT_EXPIRED_CLIENT,
                business=business,
            )
            await notifications.notify_staff(
                bot, session, booking_id=booking.id, template=texts.NOTIFY_EXPIRED,
                admin_ids=admin_ids, business=business, demo_mode=demo_mode,
            )
    return len(expired)
