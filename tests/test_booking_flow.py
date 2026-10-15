"""Наскрізні тести сценарію запису через Dispatcher (FSM, фільтри, middleware)."""

from datetime import UTC, datetime, timedelta

from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import select

from bot import texts
from bot.db.models import Booking, BookingStatus, Client, Master, Service
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import MyBookingCb
from tests.fake_telegram import TelegramUser


async def _bookings(engine) -> list[Booking]:
    async with create_session_factory(engine)() as session:
        return list((await session.scalars(select(Booking))).all())


async def _go_to_time_choice(user, service="Стрижка · 45 хв · 500 грн", master=texts.BTN_ANY_MASTER):
    await user.send(texts.BTN_BOOK)
    await user.click(user.button(service))
    await user.click(user.button(master))
    first_date = user.buttons()[0][1]
    await user.click(first_date)


async def test_full_booking_with_phone_request(user, engine):
    await user.send("/start")
    assert "Demo Barber" in user.last_text()

    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.CHOOSE_SERVICE
    labels = [label for label, _ in user.buttons()]
    assert labels == ["Стрижка · 45 хв · 500 грн", "Борода · 30 хв · 350 грн", texts.BTN_BACK]

    await user.click(user.button("Стрижка · 45 хв · 500 грн"))
    labels = [label for label, _ in user.buttons()]
    assert labels == [texts.BTN_ANY_MASTER, "Олександр", "Дмитро", texts.BTN_BACK]  # без Максима

    await user.click(user.button("Дмитро"))
    assert user.last_text().endswith(texts.CHOOSE_DATE)
    await user.click(user.buttons()[0][1])
    assert "Оберіть час" in user.last_text()

    time_label, time_data = user.slot_buttons()[0]
    await user.click(time_data)
    assert user.last_text() == texts.ASK_PHONE
    contact_button = user.last_reply_keyboard().keyboard[0][0]
    assert contact_button.request_contact

    await user.share_contact("380501234567")
    assert "Перевірте" in user.last_text()
    assert "Дмитро" in user.last_text() and time_label in user.last_text()
    assert [label for label, _ in user.buttons()] == [texts.BTN_CONFIRM, texts.BTN_BACK]
    assert await _bookings(engine) == []  # до підтвердження нічого не створено

    await user.click(user.button(texts.BTN_CONFIRM))
    assert "Ви записані" in user.last_text()
    assert "Дмитро" in user.last_text()

    [booking] = await _bookings(engine)
    async with create_session_factory(engine)() as session:
        client = await session.get(Client, booking.client_id)
        master = await session.get(Master, booking.master_id)
    assert client.telegram_id == user.user.id and client.phone == "+380501234567"
    assert master.name == "Дмитро"


async def test_phone_is_asked_only_once(user, engine):
    await _go_to_time_choice(user)
    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_CONFIRM))

    # Другий запис: телефон уже відомий — одразу підтвердження
    await _go_to_time_choice(user)
    await user.click(user.slot_buttons()[0][1])
    assert "Перевірте" in user.last_text()
    await user.click(user.button(texts.BTN_CONFIRM))
    assert len(await _bookings(engine)) == 2


async def test_any_master_is_assigned(user, engine):
    await _go_to_time_choice(user, master=texts.BTN_ANY_MASTER)
    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380501234567")
    assert texts.ANY_MASTER_LABEL in user.last_text()

    await user.click(user.button(texts.BTN_CONFIRM))
    [booking] = await _bookings(engine)
    async with create_session_factory(engine)() as session:
        master = await session.get(Master, booking.master_id)
    assert master.name in ("Олександр", "Дмитро")
    assert master.name in user.last_text()


async def test_foreign_contact_rejected(user, engine):
    await _go_to_time_choice(user)
    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380999999999", user_id=12345)
    assert user.last_text() == texts.PHONE_NOT_OWN
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(Client)) is None


async def test_back_buttons_walk_all_steps(user):
    await _go_to_time_choice(user)
    await user.click(user.slot_buttons()[0][1])  # час
    await user.send(texts.BTN_BACK)  # з кроку телефону
    assert "Оберіть час" in user.last_text()

    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_BACK))  # з підтвердження
    assert "Оберіть час" in user.last_text()
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text().endswith(texts.CHOOSE_DATE)
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text().endswith(texts.CHOOSE_MASTER)
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text() == texts.CHOOSE_SERVICE
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text() == texts.BACK_TO_MENU


async def test_slot_taken_before_confirm(dp, engine):
    first, second = TelegramUser(dp, user_id=1, name="Перший"), TelegramUser(dp, user_id=2, name="Другий")
    for u in (first, second):
        await _go_to_time_choice(u, master="Дмитро")
        slot = u.slot_buttons()[0]
        await u.click(slot[1])
        await u.share_contact(f"+38050000000{u.user.id}")
    assert first.last_text() == second.last_text()  # обидва бачать той самий слот

    await first.click(first.button(texts.BTN_CONFIRM))
    assert "Ви записані" in first.last_text()

    await second.click(second.button(texts.BTN_CONFIRM))
    assert second.last_text().startswith(texts.SLOT_TAKEN)
    assert slot not in second.slot_buttons()  # зайнятий час більше не пропонується
    assert len(await _bookings(engine)) == 1


async def test_stale_button_is_answered(user):
    await user.send(texts.BTN_BOOK)
    service_button = user.button("Стрижка · 45 хв · 500 грн")
    await user.click(service_button)
    requests = await user.click(service_button)  # стара кнопка з попереднього кроку
    [answer] = [r for r in requests if isinstance(r, AnswerCallbackQuery)]
    assert answer.text == texts.STALE_BUTTON


async def test_every_callback_is_answered_once(user):
    await user.send(texts.BTN_BOOK)
    requests = await user.click(user.button("Стрижка · 45 хв · 500 грн"))
    assert sum(isinstance(r, AnswerCallbackQuery) for r in requests) == 1


async def test_progress_shown_on_every_step(user):
    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.CHOOSE_SERVICE  # ще нічого не обрано

    await user.click(user.button("Стрижка · 45 хв · 500 грн"))
    assert user.last_text() == f"🧾 <b>Стрижка</b>\n\n{texts.CHOOSE_MASTER}"

    await user.click(user.button("Дмитро"))
    assert user.last_text() == f"🧾 <b>Стрижка · Дмитро</b>\n\n{texts.CHOOSE_DATE}"

    date_label, date_data = user.buttons()[0]
    await user.click(date_data)
    assert user.last_text() == f"🧾 <b>Стрижка · Дмитро · {date_label}</b>\n\n{texts.CHOOSE_TIME}"

    time_label, time_data = user.slot_buttons()[0]
    await user.click(time_data)
    edited = [r.text for r in user.session.requests if getattr(r, "text", None)][-2]
    assert edited == f"🧾 <b>Стрижка · Дмитро · {date_label} · {time_label}</b>\n\n{texts.PHONE_STEP}"


async def test_day_part_header_does_nothing(user):
    await _go_to_time_choice(user)
    header = next(data for _, data in user.buttons() if data.startswith("noop"))
    requests = await user.click(header)
    [answer] = [r for r in requests if isinstance(r, AnswerCallbackQuery)]
    assert answer.text is None  # без «кнопка неактуальна»
    assert user.slot_buttons()  # усе ще на кроці вибору часу
    await user.click(user.slot_buttons()[0][1])
    assert user.last_text() == texts.ASK_PHONE


async def test_phone_typed_as_text(user, engine):
    await _go_to_time_choice(user)
    await user.click(user.slot_buttons()[0][1])
    assert "щоб майстер міг зв'язатися" in user.last_text()

    await user.send("мій номер 12345")
    assert user.last_text() == texts.PHONE_INVALID
    assert user.last_reply_keyboard().keyboard[0][0].request_contact  # кнопка на місці

    await user.send("050 123-45-67")
    assert "Перевірте" in user.last_text()
    async with create_session_factory(engine)() as session:
        client = await session.scalar(select(Client))
    assert client.phone == "+380501234567"

    await user.click(user.button(texts.BTN_CONFIRM))
    assert "Ви записані" in user.last_text()
    assert user.last_text().endswith(texts.MANAGE_BOOKING)


# --- Мої записи й контакти ----------------------------------------------------

async def _add_booking(engine, telegram_id: int, start: datetime) -> int:
    """Запис до Дмитра на «Стрижку» напряму в базі."""
    async with create_session_factory(engine)() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == telegram_id))
        if client is None:
            client = Client(telegram_id=telegram_id, name="Клієнт", phone="+380501234567")
        master = await session.scalar(select(Master).where(Master.name == "Дмитро"))
        service = await session.scalar(select(Service).where(Service.name == "Стрижка"))
        booking = Booking(
            client=client, master=master, service=service,
            start_dt=start, end_dt=start + timedelta(minutes=45),
        )
        session.add(booking)
        await session.commit()
        return booking.id


def _in(hours: float) -> datetime:
    start = datetime.now(UTC) + timedelta(hours=hours)
    return start.replace(minute=0, second=0, microsecond=0)


async def test_my_bookings_empty(user):
    await user.send(texts.BTN_MY_BOOKINGS)
    assert user.last_text() == texts.MY_BOOKINGS_EMPTY


async def test_my_bookings_cards_and_deadline(user, engine, business):
    soon = await _add_booking(engine, user.user.id, _in(1.5))  # дедлайн 2 год уже минув
    later = await _add_booking(engine, user.user.id, _in(72))
    await _add_booking(engine, 99999, _in(48))  # чужий запис

    requests = await user.send(texts.BTN_MY_BOOKINGS)
    texts_sent = [r.text for r in requests if getattr(r, "text", None)]
    assert texts_sent[0] == texts.MY_BOOKINGS_HEADER.format(count=2)

    soon_card, later_card = requests[1], requests[2]
    assert "Дмитро" in soon_card.text and business.business.phone in soon_card.text
    assert soon_card.reply_markup is None  # лише телефон, без кнопок
    labels = [b.text for row in later_card.reply_markup.inline_keyboard for b in row]
    assert labels == [texts.BTN_CANCEL_BOOKING, texts.BTN_MOVE_BOOKING]
    assert later_card.reply_markup.inline_keyboard[0][0].callback_data == MyBookingCb(
        action="cancel", booking_id=later
    ).pack()

    # Навіть якщо натиснути «Скасувати» на записі після дедлайну — не скасується
    await user.click(MyBookingCb(action="cancel_yes", booking_id=soon).pack())
    assert business.business.phone in user.last_text()
    async with create_session_factory(engine)() as session:
        assert (await session.get(Booking, soon)).status is BookingStatus.CONFIRMED


async def test_cancel_booking(user, engine):
    booking_id = await _add_booking(engine, user.user.id, _in(48))
    await user.send(texts.BTN_MY_BOOKINGS)

    await user.click(user.button(texts.BTN_CANCEL_BOOKING))
    assert user.last_text().startswith("Скасувати цей запис?")
    await user.click(user.button(texts.BTN_CANCEL_NO))
    assert [label for label, _ in user.buttons()] == [texts.BTN_CANCEL_BOOKING, texts.BTN_MOVE_BOOKING]

    await user.click(user.button(texts.BTN_CANCEL_BOOKING))
    await user.click(user.button(texts.BTN_CANCEL_YES))
    assert user.last_text().startswith("❌ Запис скасовано.")
    async with create_session_factory(engine)() as session:
        assert (await session.get(Booking, booking_id)).status is BookingStatus.CANCELLED_BY_CLIENT

    # Повторне натискання старої кнопки
    await user.click(MyBookingCb(action="cancel_yes", booking_id=booking_id).pack())
    assert user.last_text() == texts.BOOKING_GONE
    await user.send(texts.BTN_MY_BOOKINGS)
    assert user.last_text() == texts.MY_BOOKINGS_EMPTY


async def test_cannot_touch_other_clients_booking(dp, engine):
    owner, stranger = TelegramUser(dp, user_id=1), TelegramUser(dp, user_id=2)
    booking_id = await _add_booking(engine, owner.user.id, _in(48))
    for action in ("cancel", "cancel_yes", "move"):
        await stranger.click(MyBookingCb(action=action, booking_id=booking_id).pack())
        assert stranger.last_text() == texts.BOOKING_GONE
    async with create_session_factory(engine)() as session:
        assert (await session.get(Booking, booking_id)).status is BookingStatus.CONFIRMED


async def test_reschedule_booking(user, engine):
    old_start = _in(48)
    booking_id = await _add_booking(engine, user.user.id, old_start)
    await user.send(texts.BTN_MY_BOOKINGS)

    await user.click(user.button(texts.BTN_MOVE_BOOKING))
    assert user.last_text().endswith(texts.MOVE_CHOOSE_DATE)
    assert "Стрижка · Дмитро" in user.last_text()
    await user.click(user.button(texts.BTN_BACK))  # назад — знову картка
    assert [label for label, _ in user.buttons()] == [texts.BTN_CANCEL_BOOKING, texts.BTN_MOVE_BOOKING]

    await user.click(user.button(texts.BTN_MOVE_BOOKING))
    await user.click(user.buttons()[-2][1])  # остання доступна дата
    assert user.last_text().endswith(texts.MOVE_CHOOSE_TIME)
    new_label, new_data = user.slot_buttons()[-1]
    await user.click(new_data)
    assert user.last_text().startswith("Перенести запис?")
    assert "Було:" in user.last_text() and new_label in user.last_text()

    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text().endswith(texts.MOVE_CHOOSE_TIME)
    await user.click(new_data)
    await user.click(user.button(texts.BTN_MOVE_CONFIRM))
    assert user.last_text().startswith("✅ Запис перенесено!")
    assert user.last_text().endswith(texts.MANAGE_BOOKING)

    bookings = await _bookings(engine)
    assert [b.id for b in bookings] == [booking_id]
    assert bookings[0].start_dt != old_start
    assert bookings[0].status is BookingStatus.CONFIRMED


async def test_contacts(user, business):
    requests = await user.send(texts.BTN_CONTACTS)
    [answer] = [r for r in requests if getattr(r, "text", None)]
    assert "м. Львів, пл. Ринок, 1" in answer.text and business.business.phone in answer.text
    urls = [b.url for row in answer.reply_markup.inline_keyboard for b in row]
    assert urls == [business.business.map_url, business.business.instagram]
