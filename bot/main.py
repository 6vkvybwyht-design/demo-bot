"""Точка входу: python -m bot.main"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import BusinessConfig, Settings, get_settings, load_business_config
from bot.handlers import client


def create_dispatcher(settings: Settings, business: BusinessConfig) -> Dispatcher:
    # settings і business потрапляють у хендлери як іменовані аргументи
    dp = Dispatcher(settings=settings, business=business)
    dp.include_router(client.router)
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
    dp = create_dispatcher(settings, business)

    logging.info(
        "Запуск бота «%s» (demo_mode=%s)", business.business.name, settings.demo_mode
    )
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
