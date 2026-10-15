"""Щоденний бекап бази SQLite зі зберіганням останніх N копій.

Використовує вбудований механізм бекапу SQLite: копія цілісна, навіть якщо
бот саме пише в базу. Працює однаково на Windows і на сервері.
"""

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from bot.db.models import utcnow

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "bot-"
BACKUP_SUFFIX = ".db"


def sqlite_path(database_url: str) -> Path | None:
    """Шлях до файлу SQLite або None (інша СУБД чи база в пам'яті)."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite(db_path: Path, backup_dir: Path, keep: int, now: datetime) -> Path:
    """Робить копію бази й видаляє найстаріші, лишаючи `keep` останніх."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{BACKUP_PREFIX}{now:%Y-%m-%d_%H%M%S}{BACKUP_SUFFIX}"
    partial = target.with_suffix(".partial")  # недороблена копія не потрапить у ротацію

    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(partial)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    partial.replace(target)

    rotate_backups(backup_dir, keep)
    return target


def list_backups(backup_dir: Path) -> list[Path]:
    """Копії від найновішої до найстарішої (ім'я містить дату й час)."""
    return sorted(backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), reverse=True)


def rotate_backups(backup_dir: Path, keep: int) -> None:
    for old in list_backups(backup_dir)[keep:]:
        old.unlink()


async def run_backup(
    database_url: str, backup_dir: Path, keep: int, now: datetime | None = None
) -> Path | None:
    """Бекап для планувальника: не блокує бота й не падає на помилках."""
    db_path = sqlite_path(database_url)
    if db_path is None:
        logger.info("Бекап пропущено: база не SQLite (%s)", make_url(database_url).get_backend_name())
        return None
    if not db_path.exists():
        logger.warning("Бекап пропущено: файлу бази %s немає", db_path)
        return None
    try:
        target = await asyncio.to_thread(backup_sqlite, db_path, backup_dir, keep, now or utcnow())
    except (sqlite3.Error, OSError):
        logger.exception("Не вдалося зробити бекап бази %s", db_path)
        return None
    logger.info("Бекап бази збережено: %s", target)
    return target


def latest_backup_time(backup_dir: Path) -> datetime | None:
    """Час найновішої копії (з імені файлу, UTC)."""
    for path in list_backups(backup_dir):
        stamp = path.name.removeprefix(BACKUP_PREFIX).removesuffix(BACKUP_SUFFIX)
        try:
            return datetime.strptime(stamp, "%Y-%m-%d_%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


async def ensure_recent_backup(
    database_url: str, backup_dir: Path, keep: int, now: datetime | None = None
) -> Path | None:
    """На старті: якщо бот був вимкнений у час бекапу й остання копія старша
    за добу — робимо її одразу."""
    now = now or utcnow()
    latest = latest_backup_time(backup_dir)
    if latest is not None and now - latest < timedelta(days=1):
        return None
    return await run_backup(database_url, backup_dir, keep, now)
