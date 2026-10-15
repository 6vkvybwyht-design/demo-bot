"""Ручний бекап бази: python -m bot.backup"""

import asyncio
import logging
import sys

from bot.config import BaseAppSettings
from bot.services.backup import run_backup


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = BaseAppSettings()
    target = asyncio.run(run_backup(settings.database_url, settings.backup_dir, settings.backup_keep))
    if target is None:
        sys.exit(1)
    print(f"Готово: {target}")


if __name__ == "__main__":
    main()
