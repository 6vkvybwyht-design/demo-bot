"""Точка входу: python -m bot.main"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import BusinessConfig, Settings, get_settings, load_business_config
from bot.db.session import create_engine, create_session_factory
from bot.handlers import client, fallback
from bot.middlewares.db import DbSessionMiddleware


def create_dispatcher(
    settings: Settings, business: BusinessConfig, session_factory: async_sessionmaker
) -> Dispatcher:
    # settings і business потрапляють у хендлери як іменовані аргументи
    dp = Dispatcher(settings=settings, business=business)
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    # Автоматично прибирає «годинник» на натиснутій inline-кнопці
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(client.router)
    dp.include_router(fallback.router)  # завжди останнім
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    business = load_business_config(settings.business_config_path)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings.database_url)
    dp = create_dispatcher(settings, business, create_session_factory(engine))

    logging.info(
        "Запуск бота «%s» (demo_mode=%s)", business.business.name, settings.demo_mode
    )
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
