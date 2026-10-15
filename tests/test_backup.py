"""Щоденний бекап SQLite (bot/services/backup.py)."""

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from bot.config import BASE_DIR, Settings
from bot.db.session import create_session_factory
from bot.main import create_scheduler
from bot.services.backup import (
    backup_sqlite,
    ensure_recent_backup,
    latest_backup_time,
    list_backups,
    run_backup,
    sqlite_path,
)

NOW = datetime(2026, 10, 20, 0, 30, tzinfo=UTC)


def _make_db(path, rows=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    return path


def _count(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT count(*) FROM t").fetchone()[0]


def test_sqlite_path():
    assert str(sqlite_path("sqlite+aiosqlite:///data/bot.db")) == "data/bot.db"
    assert sqlite_path("sqlite+aiosqlite:///:memory:") is None
    assert sqlite_path("postgresql+asyncpg://u:p@host/db") is None


def test_backup_is_a_full_copy(tmp_path):
    db = _make_db(tmp_path / "bot.db", rows=5)
    target = backup_sqlite(db, tmp_path / "backups", keep=7, now=NOW)
    assert target.name == "bot-2026-10-20_003000.db"
    assert _count(target) == 5
    assert list((tmp_path / "backups").glob("*.partial")) == []


def test_backup_is_consistent_while_bot_writes(tmp_path):
    db = _make_db(tmp_path / "bot.db", rows=2)
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # бот саме пише й ще не закомітив
    writer.execute("INSERT INTO t VALUES (100)")
    target = backup_sqlite(db, tmp_path / "backups", keep=7, now=NOW)
    writer.execute("ROLLBACK")
    writer.close()
    assert _count(target) == 2  # лише закомічені дані


def test_keeps_last_7(tmp_path):
    db = _make_db(tmp_path / "bot.db")
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "notes.txt").write_text("не чіпати")
    for day in range(10):
        backup_sqlite(db, backups, keep=7, now=NOW + timedelta(days=day))
    names = [p.name for p in list_backups(backups)]
    assert len(names) == 7
    assert names[0] == "bot-2026-10-29_003000.db" and names[-1] == "bot-2026-10-23_003000.db"
    assert (backups / "notes.txt").exists()  # чужі файли не видаляються


async def test_run_backup_skips_without_failing(tmp_path):
    assert await run_backup("postgresql+asyncpg://u:p@h/db", tmp_path, 7) is None
    assert await run_backup(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}", tmp_path / "b", 7) is None
    assert not (tmp_path / "missing.db").exists()  # порожню базу не створюємо


async def test_run_backup(tmp_path):
    db = _make_db(tmp_path / "bot.db")
    target = await run_backup(f"sqlite+aiosqlite:///{db}", tmp_path / "backups", 7, now=NOW)
    assert target is not None and _count(target) == 3


async def test_backup_on_startup_only_if_stale(tmp_path):
    db = _make_db(tmp_path / "bot.db")
    url, backups = f"sqlite+aiosqlite:///{db}", tmp_path / "backups"
    assert await ensure_recent_backup(url, backups, 7, now=NOW) is not None  # копій ще немає
    assert latest_backup_time(backups) == NOW
    assert await ensure_recent_backup(url, backups, 7, now=NOW + timedelta(hours=20)) is None
    assert await ensure_recent_backup(url, backups, 7, now=NOW + timedelta(hours=25)) is not None
    assert len(list_backups(backups)) == 2


def test_scheduler_has_daily_backup(engine, business):
    settings = Settings(_env_file=None, bot_token="1:a")
    job = create_scheduler(None, create_session_factory(engine), business, settings=settings).get_job("backup")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("3", "30")
    assert str(job.trigger.timezone) == "Europe/Kyiv"
    assert job.kwargs["keep"] == 7


def test_manual_backup_command(tmp_path):
    db = _make_db(tmp_path / "bot.db")
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
           "BACKUP_DIR": str(tmp_path / "backups"), "BACKUP_KEEP": "3"}
    result = subprocess.run(
        [sys.executable, "-m", "bot.backup"], cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Готово:" in result.stdout
    assert len(list_backups(tmp_path / "backups")) == 1
