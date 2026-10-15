"""Точка входу: python -m bot.main"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BusinessConfig, Settings, get_settings, load_business_config
from bot.db.models import utcnow
from bot.db.session import create_engine, create_session_factory
from bot.handlers import admin, client, fallback, my_bookings
from bot.middlewares.db import DbSessionMiddleware
from bot.services.demo import ensure_demo_data, reset_demo
from bot.services.reminders import send_due_reminders


# Нічне скидання демо (час — за часовим поясом бізнесу)
DEMO_RESET_HOUR = 4


def create_scheduler(
    bot: Bot, session_factory: async_sessionmaker, business: BusinessConfig, demo_mode: bool = False
) -> AsyncIOScheduler:
    """Щохвилинна перевірка нагадувань (перший запуск — одразу після старту)
    і, в демо-режимі, нічне скидання даних о 04:00."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_due_reminders,
        "interval",
        minutes=1,
        kwargs={"bot": bot, "session_factory": session_factory, "business": business},
        id="reminders",
        max_instances=1,  # наступний запуск не почнеться, поки не закінчився попередній
        coalesce=True,  # пропущені запуски (бот «спав») зливаються в один
        next_run_time=utcnow(),
    )
    if demo_mode:
        scheduler.add_job(
            reset_demo,
            "cron",
            hour=DEMO_RESET_HOUR,
            minute=0,
            timezone=business.booking.tz,
            kwargs={"session_factory": session_factory, "business": business},
            id="demo_reset",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,  # якщо бот перезапускався близько 04:00 — усе одно скинути
        )
    return scheduler


def create_dispatcher(
    settings: Settings, business: BusinessConfig, session_factory: async_sessionmaker
) -> Dispatcher:
    # settings і business потрапляють у хендлери як іменовані аргументи
    dp = Dispatcher(settings=settings, business=business)
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    # Автоматично прибирає «годинник» на натиснутій inline-кнопці
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(client.router)
    dp.include_router(my_bookings.router)
    dp.include_router(admin.router)
    dp.include_router(fallback.router)  # завжди останнім
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Задача нагадувань іде щохвилини — без цього лог засипало б рядками «Running job»
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    settings = get_settings()
    business = load_business_config(settings.business_config_path)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    dp = create_dispatcher(settings, business, session_factory)
    scheduler = create_scheduler(bot, session_factory, business, settings.demo_mode)
    if settings.demo_mode:
        await ensure_demo_data(session_factory, business)

    logging.info(
        "Запуск бота «%s» (demo_mode=%s)", business.business.name, settings.demo_mode
    )
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
