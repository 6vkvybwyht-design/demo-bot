"""Точка входу: python -m bot.main"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import (
    BaseAppSettings,
    BusinessConfig,
    Settings,
    get_settings,
    load_business_config,
)
from bot.db.fsm_storage import DbStorage
from bot.db.models import utcnow
from bot.db.session import create_engine, create_session_factory
from bot.handlers import admin, client, fallback, my_bookings
from bot.middlewares.db import DbSessionMiddleware
from bot.services.demo import ensure_demo_data, reset_demo
from bot.services.backup import ensure_recent_backup, run_backup
from bot.services.reminders import send_due_reminders


# Нічне скидання демо й щоденний бекап (час — за часовим поясом бізнесу).
# Бекап — до скидання, щоб зберегти стан демо за минулий день.
DEMO_RESET_HOUR = 4
BACKUP_HOUR, BACKUP_MINUTE = 3, 30


def create_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker,
    business: BusinessConfig,
    demo_mode: bool = False,
    settings: BaseAppSettings | None = None,
) -> AsyncIOScheduler:
    """Щохвилинна перевірка нагадувань (перший запуск — одразу після старту),
    щоденний бекап бази о 03:30 і, в демо-режимі, нічне скидання даних о 04:00."""
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
    if settings is not None:
        scheduler.add_job(
            run_backup,
            "cron",
            hour=BACKUP_HOUR,
            minute=BACKUP_MINUTE,
            timezone=business.booking.tz,
            kwargs={
                "database_url": settings.database_url,
                "backup_dir": settings.backup_dir,
                "keep": settings.backup_keep,
            },
            id="backup",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=6 * 3600,  # ноутбук спав о 03:30 — зробити, щойно прокинеться
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
    # Стан діалогів — у базі, щоб незавершений запис пережив перезапуск бота
    dp = Dispatcher(storage=DbStorage(session_factory), settings=settings, business=business)
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    # Автоматично прибирає «годинник» на натиснутій inline-кнопці
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(client.router)
    dp.include_router(my_bookings.router)
    dp.include_router(admin.router)
    dp.include_router(fallback.router)  # завжди останнім
    return dp


def setup_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # До 5 МБ на файл, 3 старі файли — лог не з'їсть диск ноутбука
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # Задача нагадувань іде щохвилини — без цього лог засипало б рядками «Running job»
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def main() -> None:
    settings = get_settings()
    business = load_business_config(settings.business_config_path)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    dp = create_dispatcher(settings, business, session_factory)
    scheduler = create_scheduler(bot, session_factory, business, settings.demo_mode, settings)
    if settings.demo_mode:
        await ensure_demo_data(session_factory, business)
    await ensure_recent_backup(settings.database_url, settings.backup_dir, settings.backup_keep)

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
    setup_logging(BaseAppSettings().log_file)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        # Потрапить у лог-файл; скрипт запуску на Windows перезапустить бота
        logging.exception("Бот зупинився через помилку")
        sys.exit(1)
