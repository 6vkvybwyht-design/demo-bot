"""Нагадування (етап 7). Поточний час підміняється явним параметром `now`."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import select

from bot import texts
from bot.config import Reminders
from bot.db.models import Booking, BookingStatus, Client, Master, Service
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb, MyBookingCb
from bot.services import booking as booking_service
from bot.services.reminders import ReminderKind, decide, pending_bookings, send_due_reminders
from tests.conftest import ADMIN_ID, MASTER_TG_ID
from tests.fake_telegram import TelegramUser

CLIENT_ID = 777
# Візит у вівторок 20.10.2026 о 15:00 за Києвом (12:00 UTC)
VISIT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def factory(engine):
    return create_session_factory(engine)


@pytest.fixture
def client_user(dp):
    return TelegramUser(dp, user_id=CLIENT_ID)


async def _add(factory, start=VISIT, *, created=None, status=BookingStatus.CONFIRMED,
               reminded_24h=False, reminded_2h=False) -> int:
    async with factory() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == CLIENT_ID))
        if client is None:
            client = Client(telegram_id=CLIENT_ID, name="Іван", phone="+380501234567")
        booking = Booking(
            client=client,
            master=await session.scalar(select(Master).where(Master.name == "Дмитро")),
            service=await session.scalar(select(Service).where(Service.name == "Стрижка")),
            start_dt=start, end_dt=start + timedelta(minutes=45), status=status,
            created_at=created or start - timedelta(days=3),
            reminded_24h=reminded_24h, reminded_2h=reminded_2h,
        )
        session.add(booking)
        await session.commit()
        return booking.id


async def _run(user: TelegramUser, factory, business, now: datetime) -> list[str]:
    """Запускає задачу нагадувань у момент `now`; повертає тексти, надіслані клієнту."""
    before = len(user.session.requests)
    await send_due_reminders(user.bot, factory, business, now=now)
    return [r.text for r in user.session.requests[before:]
            if isinstance(r, SendMessage) and r.chat_id == CLIENT_ID]


async def _flags(factory, booking_id):
    async with factory() as session:
        b = await session.get(Booking, booking_id)
        return b.reminded_24h, b.reminded_2h


# --- Коли надсилати -----------------------------------------------------------


async def test_24h_reminder_window(client_user, factory, business):
    booking_id = await _add(factory)
    assert await _run(client_user, factory, business, VISIT - timedelta(hours=24, minutes=1)) == []
    sent = await _run(client_user, factory, business, VISIT - timedelta(hours=24))
    assert len(sent) == 1 and sent[0].startswith("⏰ <b>Нагадуємо про ваш запис: Вт, 20.10, 15:00</b>")
    assert await _flags(factory, booking_id) == (True, False)
    # Наступні запуски того ж дня — без повторів
    for minutes in (1, 2, 60, 600):
        assert await _run(client_user, factory, business, VISIT - timedelta(hours=24) + timedelta(minutes=minutes)) == []


async def test_2h_reminder(client_user, factory, business):
    booking_id = await _add(factory, reminded_24h=True)
    assert await _run(client_user, factory, business, VISIT - timedelta(hours=2, minutes=1)) == []
    sent = await _run(client_user, factory, business, VISIT - timedelta(hours=2))
    assert len(sent) == 1
    assert await _flags(factory, booking_id) == (True, True)
    assert await _run(client_user, factory, business, VISIT - timedelta(minutes=90)) == []


async def test_both_reminders_over_time(client_user, factory, business):
    await _add(factory)
    sent = []
    now = VISIT - timedelta(days=2)
    while now < VISIT + timedelta(hours=1):  # щохвилинна задача протягом двох діб
        sent += await _run(client_user, factory, business, now)
        now += timedelta(minutes=15)
    assert len(sent) == 2


async def test_missed_24h_sends_only_current_2h(client_user, factory, business):
    """Бот був вимкнений: після запуску до візиту 1 год — надсилаємо лише «за 2 год»."""
    booking_id = await _add(factory)
    sent = await _run(client_user, factory, business, VISIT - timedelta(hours=1))
    assert len(sent) == 1
    assert await _flags(factory, booking_id) == (True, True)
    assert await _run(client_user, factory, business, VISIT - timedelta(minutes=59)) == []


async def test_missed_24h_still_sent_if_more_than_2h_left(client_user, factory, business):
    """Бот прокинувся за 5 год до візиту: «за 24 год» ще актуальне, «за 2 год» прийде пізніше."""
    booking_id = await _add(factory)
    assert len(await _run(client_user, factory, business, VISIT - timedelta(hours=5))) == 1
    assert await _flags(factory, booking_id) == (True, False)
    assert len(await _run(client_user, factory, business, VISIT - timedelta(hours=2))) == 1


async def test_no_reminder_after_visit_started(client_user, factory, business):
    booking_id = await _add(factory)
    for now in (VISIT, VISIT + timedelta(minutes=10), VISIT + timedelta(days=1)):
        assert await _run(client_user, factory, business, now) == []
    assert await _flags(factory, booking_id) == (False, False)


@pytest.mark.parametrize(
    "status", [BookingStatus.CANCELLED_BY_CLIENT, BookingStatus.CANCELLED_BY_ADMIN]
)
async def test_cancelled_bookings_get_no_reminders(client_user, factory, business, status):
    await _add(factory, status=status)
    for hours in (24, 23, 2, 1):
        assert await _run(client_user, factory, business, VISIT - timedelta(hours=hours)) == []


async def test_reminders_disabled_in_config(client_user, factory, business):
    config = business.model_copy(update={"reminders": Reminders(before_24h=False, before_2h=True)})
    booking_id = await _add(factory)
    assert await _run(client_user, factory, config, VISIT - timedelta(hours=20)) == []
    assert await _flags(factory, booking_id) == (True, False)  # позначено, щоб не перевіряти щоразу
    assert len(await _run(client_user, factory, config, VISIT - timedelta(hours=2))) == 1


async def test_booking_created_less_than_2h_before_gets_nothing(client_user, factory, business):
    now = VISIT - timedelta(minutes=90)
    async with factory() as session:
        client = await booking_service.get_or_create_client(session, CLIENT_ID, "Іван")
        service = await session.scalar(select(Service).where(Service.name == "Стрижка"))
        master = await session.scalar(select(Master).where(Master.name == "Дмитро"))
        await booking_service.create_booking(
            session, client=client, service=service, master_id=master.id, start=VISIT,
            rules=business.booking, now=now, reminders=business.reminders,
        )
    for minutes in (0, 30, 60, 89):
        assert await _run(client_user, factory, business, now + timedelta(minutes=minutes)) == []


async def test_reschedule_resets_flags_for_new_time(client_user, factory, business):
    booking_id = await _add(factory)
    # Обидва нагадування про старий час уже надіслані
    await _run(client_user, factory, business, VISIT - timedelta(hours=23))
    await _run(client_user, factory, business, VISIT - timedelta(hours=1, minutes=59))
    assert await _flags(factory, booking_id) == (True, True)

    # Перенесення на 3 дні пізніше (за 5 год до старого візиту — дедлайн ще не минув)
    new_start = VISIT + timedelta(days=3)
    moved_at = VISIT - timedelta(hours=5)
    async with factory() as session:
        await booking_service.reschedule_booking(
            session, booking_id=booking_id, telegram_id=CLIENT_ID, new_start=new_start,
            rules=business.booking, now=moved_at, reminders=business.reminders,
        )
    assert await _flags(factory, booking_id) == (False, False)
    assert await _run(client_user, factory, business, VISIT - timedelta(hours=1)) == []  # старий час
    assert len(await _run(client_user, factory, business, new_start - timedelta(hours=24))) == 1
    assert len(await _run(client_user, factory, business, new_start - timedelta(hours=2))) == 1


async def test_reschedule_to_less_than_2h_gets_no_reminders(client_user, factory, business):
    booking_id = await _add(factory, start=VISIT + timedelta(days=2))
    moved_at = VISIT - timedelta(minutes=90)  # новий час — за 1,5 год
    async with factory() as session:
        await booking_service.reschedule_booking(
            session, booking_id=booking_id, telegram_id=CLIENT_ID, new_start=VISIT,
            rules=business.booking, now=moved_at, reminders=business.reminders,
        )
    assert await _flags(factory, booking_id) == (True, True)
    assert await _run(client_user, factory, business, moved_at + timedelta(minutes=1)) == []


async def test_no_duplicates_after_restart(client_user, dp, factory, business, engine):
    await _add(factory)
    now = VISIT - timedelta(hours=10)
    assert len(await _run(client_user, factory, business, now)) == 1
    # «Перезапуск»: новий бот і нова фабрика сесій до тієї ж бази
    restarted = TelegramUser(dp, user_id=CLIENT_ID)
    assert await _run(restarted, create_session_factory(engine), business, now) == []


async def test_blocked_client_is_logged_and_others_still_get_reminders(client_user, factory, business, caplog):
    blocked = await _add(factory)
    async with factory() as session:  # другий клієнт на інший час того ж дня
        other = Client(telegram_id=888, name="Петро", phone="+380671112233")
        session.add(other)
        await session.commit()
        booking = Booking(
            client=other,
            master=await session.scalar(select(Master).where(Master.name == "Олександр")),
            service=await session.scalar(select(Service).where(Service.name == "Стрижка")),
            start_dt=VISIT, end_dt=VISIT + timedelta(minutes=45),
        )
        session.add(booking)
        await session.commit()
    client_user.session.blocked_chats.add(CLIENT_ID)

    with caplog.at_level(logging.WARNING):
        sent = await send_due_reminders(client_user.bot, factory, business, now=VISIT - timedelta(hours=10))
    assert sent == 1  # лише другому клієнту
    assert any(f"записом {blocked}" in r.getMessage() for r in caplog.records)
    assert await _flags(factory, blocked) == (True, False)  # не повторюємо щохвилини


# --- Кнопки в нагадуванні -----------------------------------------------------


def _buttons(request: SendMessage) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in request.reply_markup.inline_keyboard for b in row]


async def _reminder(user, factory, business, now) -> SendMessage:
    await send_due_reminders(user.bot, factory, business, now=now)
    return [r for r in user.session.requests if isinstance(r, SendMessage) and r.chat_id == CLIENT_ID][-1]


async def test_reminder_buttons_respect_cancel_deadline(client_user, factory, business):
    booking_id = await _add(factory)
    day_before = await _reminder(client_user, factory, business, VISIT - timedelta(hours=23))
    assert _buttons(day_before) == [
        (texts.BTN_WILL_COME, MyBookingCb(action="attend", booking_id=booking_id).pack()),
        (texts.BTN_CANCEL_BOOKING, MyBookingCb(action="cancel", booking_id=booking_id).pack()),
    ]
    two_hours = await _reminder(client_user, factory, business, VISIT - timedelta(hours=2))
    assert [label for label, _ in _buttons(two_hours)] == [texts.BTN_WILL_COME]  # скасувати вже пізно
    assert business.business.phone in two_hours.text


def _soon(hours: float) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


async def test_will_come_marks_booking_and_admin_sees_it(client_user, owner, factory, business):
    start = _soon(20)
    booking_id = await _add(factory, start=start)
    reminder = await _reminder(client_user, factory, business, start - timedelta(hours=19))

    await client_user.click(_buttons(reminder)[0][1])  # «Буду ✅»
    assert client_user.last_text().endswith(texts.ATTENDANCE_CONFIRMED)
    async with factory() as session:
        assert (await session.get(Booking, booking_id)).client_confirmed_at is not None

    await owner.click(AdminCb(action="bk", id=booking_id).pack())
    assert texts.ADMIN_CLIENT_CONFIRMED in owner.last_text()
    day = start.astimezone(business.booking.tz).date().isoformat()
    await owner.click(AdminCb(action="day", arg=day).pack())
    assert f"{texts.STATUS_ICON_CLIENT_CONFIRMED} " in owner.last_text()


async def test_cancel_from_reminder(client_user, factory, business):
    start = _soon(20)
    booking_id = await _add(factory, start=start)
    reminder = await _reminder(client_user, factory, business, start - timedelta(hours=19))

    await client_user.click(_buttons(reminder)[1][1])  # «Скасувати»
    assert client_user.last_text().startswith("Скасувати цей запис?")
    await client_user.click(client_user.button(texts.BTN_CANCEL_YES))
    assert client_user.last_text().startswith("❌ Запис скасовано.")
    async with factory() as session:
        assert (await session.get(Booking, booking_id)).status is BookingStatus.CANCELLED_BY_CLIENT
    assert client_user.sent_to(ADMIN_ID) and client_user.sent_to(MASTER_TG_ID)


async def test_cancel_from_old_reminder_after_deadline(client_user, factory, business):
    """Кнопка з нагадування за добу, натиснута за годину до візиту: дедлайн той самий, що в «Мої записи»."""
    start = _soon(1.5)
    booking_id = await _add(factory, start=start)
    await client_user.click(MyBookingCb(action="cancel", booking_id=booking_id).pack())
    assert business.business.phone in client_user.last_text()
    async with factory() as session:
        assert (await session.get(Booking, booking_id)).status is BookingStatus.CONFIRMED


async def test_will_come_on_cancelled_booking(client_user, factory):
    booking_id = await _add(factory, start=_soon(20), status=BookingStatus.CANCELLED_BY_ADMIN)
    await client_user.click(MyBookingCb(action="attend", booking_id=booking_id).pack())
    assert client_user.last_text() == texts.BOOKING_GONE


async def test_reschedule_clears_client_confirmation(client_user, factory, business):
    start = _soon(48)
    booking_id = await _add(factory, start=start)
    async with factory() as session:
        await booking_service.confirm_attendance(
            session, booking_id=booking_id, telegram_id=CLIENT_ID, now=datetime.now(UTC)
        )
        await booking_service.reschedule_booking(
            session, booking_id=booking_id, telegram_id=CLIENT_ID, new_start=start + timedelta(hours=1),
            rules=business.booking, now=datetime.now(UTC), reminders=business.reminders,
        )
        assert (await session.get(Booking, booking_id)).client_confirmed_at is None



# --- Кожен рівень захисту окремо ----------------------------------------------


@pytest.mark.parametrize(
    ("left", "flags", "expected"),
    [
        (timedelta(minutes=-5), (False, False), (None, False, False)),  # візит почався
        (timedelta(0), (False, False), (None, False, False)),
        (timedelta(hours=1), (False, False), (ReminderKind.BEFORE_2H, True, True)),
        (timedelta(hours=1), (True, True), (None, False, False)),
        (timedelta(hours=5), (False, False), (ReminderKind.BEFORE_24H, True, False)),
        (timedelta(hours=5), (True, False), (None, False, False)),
        (timedelta(hours=25), (False, False), (None, False, False)),
    ],
)
def test_decide(left, flags, expected):
    booking = Booking(start_dt=VISIT, reminded_24h=flags[0], reminded_2h=flags[1])
    decision = decide(booking, VISIT - left, Reminders())
    assert (decision.send, decision.mark_24h, decision.mark_2h) == expected


async def test_pending_bookings_query(dp, factory):  # dp — тестовий салон у базі
    started = await _add(factory, start=VISIT - timedelta(minutes=10))
    cancelled = await _add(factory, status=BookingStatus.CANCELLED_BY_CLIENT)
    done = await _add(factory, reminded_24h=True, reminded_2h=True)
    far = await _add(factory, start=VISIT + timedelta(days=2))
    due = await _add(factory)
    async with factory() as session:
        found = {b.id for b in await pending_bookings(session, VISIT - timedelta(hours=3))}
    assert due in found and started in found  # о цій порі візит ще не почався
    async with factory() as session:
        found = {b.id for b in await pending_bookings(session, VISIT - timedelta(minutes=5))}
    assert found == {due}
    assert not {cancelled, done, far} & found
