"""Наскрізні тести адмінки й сповіщень через Dispatcher."""

import logging
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select

from bot import texts
from bot.db.models import Booking, BookingStatus, Client, Master, Service, TimeOff, WorkingHours
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb
from bot.states import AdminStates
from tests.conftest import ADMIN_ID, MASTER_TG_ID
from tests.fake_telegram import TelegramUser

CLIENT_ID = 777


def _cb(action: str, id: int = 0, arg: str = "") -> str:
    return AdminCb(action=action, id=id, arg=arg).pack()


def _in(hours: float) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


async def _add_booking(engine, start: datetime, master="Дмитро", service="Стрижка",
                       telegram_id=CLIENT_ID) -> int:
    async with create_session_factory(engine)() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == telegram_id))
        if client is None:
            client = Client(telegram_id=telegram_id, name="Іван", phone="+380501234567")
        svc = await session.scalar(select(Service).where(Service.name == service))
        booking = Booking(
            client=client,
            master=await session.scalar(select(Master).where(Master.name == master)),
            service=svc, start_dt=start, end_dt=start + timedelta(minutes=svc.duration_min),
        )
        session.add(booking)
        await session.commit()
        return booking.id


async def _get(engine, model, id_):
    async with create_session_factory(engine)() as session:
        return await session.get(model, id_)


async def _id_of(engine, model, name):
    async with create_session_factory(engine)() as session:
        return await session.scalar(select(model.id).where(model.name == name))


# --- Доступ -------------------------------------------------------------------


def _admin_actions() -> set[str]:
    """Усі дії адмінки — прямо з коду хендлерів, щоб нові кнопки теж потрапили в тест."""
    source = Path("bot/handlers/admin.py").read_text(encoding="utf-8")
    actions = set(re.findall(r'F\.action == "(\w+)"', source))
    for group in re.findall(r"F\.action\.in_\(\{([^}]*)\}\)", source):
        actions |= set(re.findall(r'"(\w+)"', group))
    return actions


def test_admin_actions_found():
    assert {"bk_cancel_yes", "svc_hide_force", "mst_svc_force", "force", "bk_done"} <= _admin_actions()


async def test_non_admin_cannot_open_admin(user):
    await user.send("/admin")
    assert user.last_text() == texts.ADMIN_ONLY


async def test_non_admin_forged_buttons_do_nothing(dp, user, engine):
    booking_id = await _add_booking(engine, _in(48))
    past_id = await _add_booking(engine, _in(-3))
    service_id = await _id_of(engine, Service, "Стрижка")
    master_id = await _id_of(engine, Master, "Дмитро")
    ids = {"bk_done": past_id, "bk_noshow": past_id, "mst_svc": master_id,
           "mst_svc_force": master_id, "svc_hide_force": service_id, "svc_hide": service_id}
    args = {"day": "2026-10-19", "sched_day": "0", "svc_edit": "name", "mst_edit": "name",
            "mst_svc": str(service_id), "mst_svc_force": str(service_id)}

    for action in sorted(_admin_actions()):
        requests = await user.click(_cb(action, ids.get(action, booking_id), args.get(action, "")))
        answers = [r for r in requests if isinstance(r, AnswerCallbackQuery)]
        edits = [r for r in requests if isinstance(r, (EditMessageText, SendMessage))]
        assert edits == [], action
        assert len(answers) == 1, action
        # «force» без стану підтвердження не доходить до адмінки — відповідь «кнопка неактуальна»
        assert answers[0].text in (texts.ADMIN_NO_ACCESS, texts.STALE_BUTTON), action

    # Навіть якщо якось опинитися в стані введення адмінки — текст не обробиться
    state = dp.fsm.resolve_context(user.bot, chat_id=user.chat.id, user_id=user.user.id)
    await state.set_state(AdminStates.input)
    await state.set_data({"kind": "svc_new_price", "name": "Хак", "duration": 30})
    await user.send("100")
    assert user.last_text() == texts.ADMIN_ONLY

    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CONFIRMED
    assert (await _get(engine, Booking, past_id)).status is BookingStatus.CONFIRMED
    assert (await _get(engine, Service, service_id)).is_active
    assert (await _get(engine, Master, master_id)).is_active
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Service)) == 2
        master = await session.scalar(select(Master).where(Master.id == master_id))
        assert await session.scalar(
            select(func.count()).select_from(Master.services.property.secondary)
            .where(Master.services.property.secondary.c.master_id == master.id)
        ) == 1


# --- Записи -------------------------------------------------------------------


async def test_day_view_and_mark(owner, engine):
    past_id = await _add_booking(engine, _in(-3))
    day = (_in(-3)).astimezone(owner.dp["business"].booking.tz).date()
    await owner.send("/admin")
    assert owner.last_text() == texts.ADMIN_MENU

    await owner.click(_cb("day", arg=day.isoformat()))
    assert "Дмитро" in owner.last_text() and "Іван" in owner.last_text()
    await owner.click(next(data for label, data in owner.buttons() if "Іван" in label))
    assert "підтверджено" in owner.last_text()
    labels = [label for label, _ in owner.buttons()]
    assert texts.BTN_MARK_COMPLETED in labels and texts.BTN_ADMIN_CANCEL not in labels  # уже почався

    await owner.click(owner.button(texts.BTN_MARK_NO_SHOW))
    assert "не прийшов" in owner.last_text()
    assert (await _get(engine, Booking, past_id)).status is BookingStatus.NO_SHOW

    await owner.click(_cb("week"))
    assert owner.last_text() == texts.ADMIN_WEEK
    assert len(owner.buttons()) == 8  # 7 днів + меню


async def test_admin_cancel_notifies_client_and_master(owner, engine):
    booking_id = await _add_booking(engine, _in(48))
    await owner.click(_cb("bk", booking_id))
    await owner.click(owner.button(texts.BTN_ADMIN_CANCEL))
    assert owner.last_text().startswith("Скасувати цей запис?")
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CONFIRMED

    await owner.click(owner.button(texts.BTN_ADMIN_CANCEL_YES))
    assert texts.CLIENT_NOTIFIED in owner.last_text()
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CANCELLED_BY_ADMIN

    [to_client] = owner.sent_to(CLIENT_ID)
    assert to_client.startswith("😔 На жаль, ваш запис скасовано.")
    [to_master] = owner.sent_to(MASTER_TG_ID)
    assert "скасовано адміністратором" in to_master
    assert owner.sent_to(ADMIN_ID) == []  # адмін, що скасував, сповіщення собі не отримує

    # Повторне підтвердження старою кнопкою — нічого не ламає
    await owner.click(_cb("bk_cancel_yes", booking_id))
    assert "скасовано адміністратором" in owner.last_text()
    assert len(owner.sent_to(CLIENT_ID)) == 1


async def test_admin_cancel_when_client_blocked_bot(owner, engine, caplog):
    booking_id = await _add_booking(engine, _in(48))
    owner.session.blocked_chats.add(CLIENT_ID)
    await owner.click(_cb("bk", booking_id))
    await owner.click(owner.button(texts.BTN_ADMIN_CANCEL))
    with caplog.at_level(logging.WARNING):
        await owner.click(owner.button(texts.BTN_ADMIN_CANCEL_YES))

    assert "Не вдалося сповістити клієнта" in owner.last_text()
    assert "+380501234567" in owner.last_text()
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CANCELLED_BY_ADMIN
    assert any(str(CLIENT_ID) in r.getMessage() for r in caplog.records)
    assert owner.sent_to(MASTER_TG_ID)  # майстер усе одно отримав сповіщення


# --- Попередження про майбутні записи -----------------------------------------


async def test_hide_service_with_future_bookings_warns(owner, engine):
    booking_id = await _add_booking(engine, _in(48))
    service_id = await _id_of(engine, Service, "Стрижка")
    await owner.click(_cb("svc", service_id))
    await owner.click(owner.button(texts.BTN_HIDE))
    assert owner.last_text().startswith("⚠️ На послугу «Стрижка» є майбутні записи: 1")
    assert "Іван, +380501234567" in owner.last_text()
    assert (await _get(engine, Service, service_id)).is_active  # ще не приховано

    await owner.click(owner.button(texts.BTN_FORCE_HIDE))
    assert texts.STATUS_HIDDEN in owner.last_text()
    assert not (await _get(engine, Service, service_id)).is_active
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CONFIRMED
    assert owner.sent_to(CLIENT_ID) == []  # клієнтів автоматично не сповіщаємо


async def test_hide_without_bookings_and_show_again(owner, engine):
    service_id = await _id_of(engine, Service, "Борода")
    await owner.click(_cb("svc", service_id))
    await owner.click(owner.button(texts.BTN_HIDE))
    assert texts.STATUS_HIDDEN in owner.last_text()
    await owner.click(owner.button(texts.BTN_SHOW))
    assert texts.STATUS_VISIBLE in owner.last_text()
    assert (await _get(engine, Service, service_id)).is_active


async def test_hide_master_with_future_bookings_warns(owner, engine):
    booking_id = await _add_booking(engine, _in(48))
    master_id = await _id_of(engine, Master, "Дмитро")
    await owner.click(_cb("mst", master_id))
    await owner.click(owner.button(texts.BTN_HIDE))
    assert owner.last_text().startswith("⚠️ У майстра «Дмитро» є майбутні записи: 1")
    await owner.click(owner.button(texts.BTN_ABORT))
    assert (await _get(engine, Master, master_id)).is_active

    await owner.click(owner.button(texts.BTN_HIDE))
    await owner.click(owner.button(texts.BTN_FORCE_HIDE))
    assert not (await _get(engine, Master, master_id)).is_active
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CONFIRMED


async def test_unlink_service_with_future_bookings_warns(owner, engine):
    await _add_booking(engine, _in(48))
    master_id = await _id_of(engine, Master, "Дмитро")
    await owner.click(_cb("mst_svcs", master_id))
    assert [label for label, _ in owner.buttons()][:2] == ["✅ Стрижка", "▫️ Борода"]

    await owner.click(owner.button("✅ Стрижка"))
    assert owner.last_text().startswith("⚠️ У майстра «Дмитро» є майбутні записи на «Стрижка»")
    await owner.click(owner.button(texts.BTN_FORCE_UNLINK))
    assert [label for label, _ in owner.buttons()][:2] == ["▫️ Стрижка", "▫️ Борода"]

    await owner.click(owner.button("▫️ Борода"))  # прив'язати — без попереджень
    assert [label for label, _ in owner.buttons()][:2] == ["▫️ Стрижка", "✅ Борода"]


async def test_schedule_change_with_conflicts(owner, engine, business):
    start = _in(48)
    await _add_booking(engine, start)
    tz = business.booking.tz
    weekday = start.astimezone(tz).weekday()
    master_id = await _id_of(engine, Master, "Дмитро")

    await owner.click(_cb("sched_m", master_id))
    await owner.click(_cb("sched_day", master_id, str(weekday)))
    await owner.send("25:00-26:00")
    assert owner.last_text().startswith("❗️ Не вдалося розпізнати")

    await owner.send("вихідний")
    assert owner.last_text().startswith("⚠️ Новий графік")
    assert "Іван, +380501234567" in owner.last_text()
    async with create_session_factory(engine)() as session:  # ще не збережено
        assert await session.scalar(select(WorkingHours).where(
            WorkingHours.master_id == master_id, WorkingHours.weekday == weekday))

    await owner.click(owner.button(texts.BTN_FORCE_SAVE))
    assert owner.last_text().startswith("🕐 <b>Графік: Дмитро</b>")
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(WorkingHours).where(
            WorkingHours.master_id == master_id, WorkingHours.weekday == weekday)) is None

    # Без конфліктів зберігається одразу
    other = (weekday + 1) % 7
    await owner.click(_cb("sched_day", master_id, str(other)))
    await owner.send("10:00-20:00 14:00-15:00")
    assert texts.SCHEDULE_SAVED in owner.sent_to(ADMIN_ID)
    async with create_session_factory(engine)() as session:
        wh = await session.scalar(select(WorkingHours).where(
            WorkingHours.master_id == master_id, WorkingHours.weekday == other))
    assert (wh.start_time, wh.break_start) == (time(10), time(14))


async def test_time_off_with_conflicts(owner, engine, business):
    start = _in(48)
    booking_id = await _add_booking(engine, start)
    local = start.astimezone(business.booking.tz)
    master_id = await _id_of(engine, Master, "Дмитро")

    await owner.click(_cb("off_m", master_id))
    await owner.send("завтра")
    assert owner.last_text().startswith("❗️ Не вдалося розпізнати")
    await owner.send(f"{local:%d.%m}")  # увесь день
    assert owner.last_text() == texts.ASK_REASON
    await owner.send("Лікарняний")
    assert owner.last_text().startswith("⚠️ Блокування перетинається з майбутніми записами: 1")

    await owner.click(owner.button(texts.BTN_FORCE_SAVE))
    assert owner.last_text().startswith("⛔ Час заблоковано: Дмитро")
    async with create_session_factory(engine)() as session:
        [time_off] = (await session.scalars(select(TimeOff))).all()
    assert time_off.reason == "Лікарняний" and time_off.start_dt <= start < time_off.end_dt
    assert (await _get(engine, Booking, booking_id)).status is BookingStatus.CONFIRMED


async def test_time_off_without_conflicts_and_reason(owner, engine, business):
    master_id = await _id_of(engine, Master, "Олександр")
    day = _in(72).astimezone(business.booking.tz)
    await owner.click(_cb("off_m", master_id))
    await owner.send(f"{day:%d.%m} 10:00-13:00")
    await owner.click(owner.button(texts.BTN_NO_REASON))
    assert owner.last_text().startswith("⛔ Час заблоковано: Олександр")
    async with create_session_factory(engine)() as session:
        [time_off] = (await session.scalars(select(TimeOff))).all()
    assert time_off.reason is None and time_off.end_dt - time_off.start_dt == timedelta(hours=3)


# --- Послуги й майстри --------------------------------------------------------


async def test_add_and_edit_service(owner, engine):
    await owner.click(_cb("svcs"))
    await owner.click(owner.button(texts.BTN_ADD_SERVICE))
    await owner.send("Укладка")
    await owner.send("пів години")
    assert owner.last_text() == texts.INVALID_INPUT.format(hint=texts.HINT_DURATION)
    await owner.send("30")
    await owner.send("300")
    assert owner.last_text().startswith("💈 <b>Укладка</b>\n⏱ 30 хв · 💵 300 грн")

    await owner.click(owner.button(texts.BTN_EDIT_PRICE))
    await owner.send("350")
    assert "350 грн" in owner.last_text()
    async with create_session_factory(engine)() as session:
        svc = await session.scalar(select(Service).where(Service.name == "Укладка"))
    assert (svc.duration_min, svc.price, svc.is_active) == (30, 350, True)


async def test_cancel_input(owner, engine):
    await owner.click(_cb("svc_add"))
    await owner.click(owner.button(texts.BTN_CANCEL_INPUT))
    assert owner.last_text() == texts.INPUT_CANCELLED
    await owner.send("Укладка")  # більше не сприймається як назва
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Service)) == 2


async def test_add_master_and_set_telegram_id(owner, engine):
    await owner.click(_cb("masters"))
    await owner.click(owner.button(texts.BTN_ADD_MASTER))
    await owner.send("Віталій")
    assert owner.last_text().startswith("👤 <b>Віталій</b>")
    await owner.click(owner.button(texts.BTN_EDIT_TELEGRAM_ID))
    await owner.send("@vitaliy")
    assert owner.last_text() == texts.INVALID_INPUT.format(hint=texts.HINT_TELEGRAM_ID)
    await owner.send("123456789")
    assert "Telegram ID: 123456789" in owner.last_text()


# --- Сповіщення про дії клієнта -----------------------------------------------


async def _book_via_bot(user: TelegramUser, date_index: int = 0) -> None:
    await user.send(texts.BTN_BOOK)
    await user.click(user.button("Стрижка · 45 хв · 500 грн"))
    await user.click(user.button("Дмитро"))
    await user.click(user.buttons()[date_index][1])
    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_CONFIRM))


async def test_new_booking_notifies_owner_and_master(user):
    await _book_via_bot(user)
    assert "Ви записані" in user.last_text()
    [to_admin] = user.sent_to(ADMIN_ID)
    [to_master] = user.sent_to(MASTER_TG_ID)
    assert to_admin == to_master
    assert to_admin.startswith("🆕 <b>Новий запис</b>")
    assert "Дмитро" in to_admin and "+380501234567" in to_admin


async def test_client_cancel_and_reschedule_notify_staff(user):
    await _book_via_bot(user, date_index=-2)  # остання дата: дедлайн скасування ще далеко
    await user.send(texts.BTN_MY_BOOKINGS)
    await user.click(user.button(texts.BTN_MOVE_BOOKING))
    await user.click(user.buttons()[-2][1])
    await user.click(user.slot_buttons()[-1][1])
    await user.click(user.button(texts.BTN_MOVE_CONFIRM))
    assert user.sent_to(ADMIN_ID)[-1].startswith("🔁 <b>Запис перенесено</b>\nБуло:")

    await user.send(texts.BTN_MY_BOOKINGS)
    await user.click(user.button(texts.BTN_CANCEL_BOOKING))
    await user.click(user.button(texts.BTN_CANCEL_YES))
    assert user.sent_to(ADMIN_ID)[-1].startswith("❌ <b>Клієнт скасував запис</b>")
    assert len(user.sent_to(MASTER_TG_ID)) == 3  # новий, перенесення, скасування


async def test_blocked_staff_does_not_break_booking(user, engine, caplog):
    user.session.blocked_chats.update({ADMIN_ID, MASTER_TG_ID})
    with caplog.at_level(logging.WARNING):
        await _book_via_bot(user)
    assert "Ви записані" in user.last_text()
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Booking)) == 1
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert str(ADMIN_ID) in logged and str(MASTER_TG_ID) in logged
