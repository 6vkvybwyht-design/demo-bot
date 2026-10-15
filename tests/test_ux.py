"""Тести правок UX: частини дня, нагадування, екран успішного запису."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot import texts
from bot.config import Reminders
from bot.keyboards.booking import times_kb
from bot.services.booking import PlannedReminders, planned_reminders
from bot.services.formatting import booking_done_text, progress_text
from bot.services.slots import DayPart, day_part, group_by_day_part

KYIV = ZoneInfo("Europe/Kyiv")
DAY = date(2026, 10, 19)


def kyiv(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=KYIV).astimezone(UTC)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (10, 0, DayPart.MORNING),
        (11, 45, DayPart.MORNING),
        (12, 0, DayPart.AFTERNOON),
        (16, 45, DayPart.AFTERNOON),
        (17, 0, DayPart.EVENING),
        (19, 30, DayPart.EVENING),
    ],
)
def test_day_part_boundaries_in_local_time(hour, minute, expected):
    assert day_part(kyiv(hour, minute), KYIV) is expected


def test_group_by_day_part_skips_empty_and_sorts():
    starts = [kyiv(18), kyiv(10), kyiv(17, 15)]
    assert group_by_day_part(starts, KYIV) == [
        (DayPart.MORNING, [kyiv(10)]),
        (DayPart.EVENING, [kyiv(17, 15), kyiv(18)]),
    ]


def test_times_keyboard_has_day_part_headers():
    starts = [kyiv(10), kyiv(10, 15), kyiv(10, 30), kyiv(10, 45), kyiv(11), kyiv(12), kyiv(17)]
    rows = [[b.text for b in row] for row in times_kb(starts, KYIV).inline_keyboard]
    assert rows == [
        [texts.DAY_PART_MORNING],
        ["10:00", "10:15", "10:30", "10:45"],
        ["11:00"],
        [texts.DAY_PART_AFTERNOON],
        ["12:00"],
        [texts.DAY_PART_EVENING],
        ["17:00"],
        [texts.BTN_BACK],
    ]


def test_progress_text_accumulates_choices():
    class Svc:
        name = "Стрижка <VIP>"

    assert progress_text("Оберіть послугу:") == "Оберіть послугу:"
    text = progress_text(
        "Оберіть час:", service=Svc(), master_name="Дмитро", day=DAY, start=kyiv(10), tz=KYIV
    )
    assert text.startswith("🧾 <b>Стрижка &lt;VIP&gt; · Дмитро · Пн, 19.10 · 10:00</b>")
    any_master = progress_text("Оберіть дату:", service=Svc(), any_master=True)
    assert texts.PROGRESS_ANY_MASTER in any_master


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
ALL = Reminders(before_24h=True, before_2h=True)


@pytest.mark.parametrize(
    ("lead", "config", "expected"),
    [
        (timedelta(days=3), ALL, PlannedReminders(True, True)),
        (timedelta(hours=24, minutes=15), ALL, PlannedReminders(True, True)),
        (timedelta(hours=5), ALL, PlannedReminders(False, True)),  # за добу вже пізно
        (timedelta(hours=2), ALL, PlannedReminders(False, True)),
        (timedelta(hours=1, minutes=59), ALL, PlannedReminders(False, False)),  # менше 2 год
        (timedelta(days=3), Reminders(before_24h=False, before_2h=True), PlannedReminders(False, True)),
        (timedelta(days=3), Reminders(before_24h=True, before_2h=False), PlannedReminders(True, False)),
    ],
)
def test_planned_reminders(lead, config, expected):
    assert planned_reminders(NOW + lead, NOW, config) == expected


@pytest.mark.parametrize(
    ("reminders", "note"),
    [
        (PlannedReminders(True, True), "Ми нагадаємо за день і за 2 години."),
        (PlannedReminders(False, True), texts.REMINDER_2H),
        (PlannedReminders(True, False), texts.REMINDER_24H),
    ],
)
def test_booking_done_text_mentions_reminders_and_my_bookings(reminders, note):
    text = booking_done_text(summary="...", address="м. Львів, пл. Ринок, 1", reminders=reminders)
    assert text.startswith("✅ Ви записані!")
    assert text.endswith(f"{note} Скасувати чи перенести запис можна в «Мої записи».")
    assert "м. Львів, пл. Ринок, 1" in text


def test_booking_done_text_without_reminders():
    text = booking_done_text(summary="...", address="addr", reminders=PlannedReminders(False, False))
    assert "нагадаємо" not in text
    assert text.endswith(texts.MANAGE_BOOKING)
