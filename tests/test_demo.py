"""Демо-режим (етап 8): маскування, сповіщення з демо-адмінки, скидання."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import func, select

from bot import texts
from bot.config import Settings
from bot.db.models import Booking, BookingStatus, Client, Master, Service, TimeOff
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb
from bot.main import create_scheduler
from bot.states import AdminStates
from bot.services.demo import (
    DEMO_CLIENT_TG_BASE,
    Viewer,
    ensure_demo_data,
    is_demo_client,
    mask_name,
    mask_phone,
    reset_demo,
    viewer_for,
)
from seed.demo_data import SERVICES
from tests.conftest import ADMIN_ID, MASTER_TG_ID
from tests.fake_telegram import TelegramUser

VISITOR = 777  # відвідувач демо (не адмін)
STRANGER = 888  # інший реальний користувач, що теж записався в демо
FAKE = DEMO_CLIENT_TG_BASE + 1  # вигаданий демо-клієнт із seed


def _cb(action: str, id: int = 0, arg: str = "") -> str:
    return AdminCb(action=action, id=id, arg=arg).pack()


def _in(hours: float) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


# --- Маскування ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("phone", "masked"),
    [
        ("+380501234593", "+380 50 *** ** 93"),
        ("+380671112233", "+380 67 *** ** 33"),
        ("+48501234567", "+485***67"),  # іноземний — перші 4 і останні 2
        ("+12345", "***"),
        (None, None),
    ],
)
def test_mask_phone(phone, masked):
    assert mask_phone(phone) == masked


@pytest.mark.parametrize(
    ("name", "masked"), [("Олександр", "Ол***"), ("Ян", "Я***"), ("І", "І***"), ("  Іван ", "Ів***")]
)
def test_mask_name(name, masked):
    assert mask_name(name) == masked


def test_viewer_rules():
    other = Client(telegram_id=STRANGER, name="Олександр", phone="+380501234593")
    own = Client(telegram_id=VISITOR, name="Іван", phone="+380671112233")
    fake = Client(telegram_id=FAKE, name="Андрій", phone="+380501112233")

    demo_owner = Viewer(user_id=VISITOR, real_admin=False, demo_mode=True)
    assert (demo_owner.client_name(other), demo_owner.client_phone(other)) == ("Ол***", "+380 50 *** ** 93")
    assert (demo_owner.client_name(own), demo_owner.client_phone(own)) == ("Іван", "+380671112233")
    assert demo_owner.client_name(fake) == "Андрій"  # вигадані демо-клієнти — як є
    assert demo_owner.may_notify(VISITOR) and not demo_owner.may_notify(STRANGER)

    real_admin = Viewer(user_id=ADMIN_ID, real_admin=True, demo_mode=True)
    assert real_admin.client_phone(other) == "+380501234593"
    assert real_admin.may_notify(STRANGER)


def test_viewer_for():
    normal = Settings(_env_file=None, bot_token="1:a", admin_ids=[ADMIN_ID])
    demo = Settings(_env_file=None, bot_token="1:a", admin_ids=[ADMIN_ID], demo_mode=True)
    assert viewer_for(VISITOR, normal) is None  # без демо — доступу немає
    assert viewer_for(ADMIN_ID, normal).is_demo_owner is False
    assert viewer_for(VISITOR, demo).is_demo_owner is True
    assert viewer_for(ADMIN_ID, demo).is_demo_owner is False
    assert is_demo_client(FAKE) and not is_demo_client(VISITOR) and not is_demo_client(None)


# --- Демо-адмінка: що бачить відвідувач ---------------------------------------


async def _add_booking(engine, telegram_id, name, phone, start, master="Дмитро") -> int:
    async with create_session_factory(engine)() as session:
        client = await session.scalar(select(Client).where(Client.telegram_id == telegram_id))
        if client is None:
            client = Client(telegram_id=telegram_id, name=name, phone=phone)
        svc = await session.scalar(select(Service).where(Service.name == "Стрижка"))
        booking = Booking(
            client=client, service=svc, start_dt=start, end_dt=start + timedelta(minutes=45),
            master=await session.scalar(select(Master).where(Master.name == master)),
        )
        session.add(booking)
        await session.commit()
        return booking.id


async def _master_id(engine, name: str) -> int:
    async with create_session_factory(engine)() as session:
        return await session.scalar(select(Master.id).where(Master.name == name))


@pytest.fixture
async def demo_bookings(demo_dp, engine):
    # Усі три записи — на один день, 12:00–14:00 за Києвом, через два дні
    tz = demo_dp["business"].booking.tz
    day = (datetime.now(UTC) + timedelta(days=2)).astimezone(tz).date()
    start = datetime(day.year, day.month, day.day, 12, tzinfo=tz).astimezone(UTC)
    return {
        "stranger": await _add_booking(engine, STRANGER, "Олександр", "+380501234593", start),
        "own": await _add_booking(engine, VISITOR, "Іван", "+380671112233", start + timedelta(hours=1)),
        "fake": await _add_booking(engine, FAKE, "Андрій", "+380501112233", start + timedelta(hours=2)),
        "day": start.astimezone(demo_dp["business"].booking.tz).date().isoformat(),
    }


@pytest.fixture
def visitor(demo_dp):
    return TelegramUser(demo_dp, user_id=VISITOR, name="Іван")


def _all_text(user: TelegramUser) -> str:
    """Усе, що бачив користувач у своєму чаті: тексти й підписи кнопок."""
    parts = []
    for r in user._own():
        parts.append(getattr(r, "text", None) or "")
        markup = getattr(r, "reply_markup", None)
        if markup is not None and hasattr(markup, "inline_keyboard"):
            parts += [b.text for row in markup.inline_keyboard for b in row]
    return "\n".join(parts)


async def test_demo_owner_button_opens_admin(visitor):
    await visitor.send("/start")
    assert texts.BTN_DEMO_OWNER in [b.text for row in visitor.last_reply_keyboard().keyboard for b in row]
    await visitor.send(texts.BTN_DEMO_OWNER)
    sent = [r.text for r in visitor._own() if isinstance(r, SendMessage)]
    assert sent[-2] == texts.DEMO_OWNER_INTRO and sent[-1] == texts.ADMIN_MENU


async def test_demo_owner_sees_others_masked(visitor, engine, demo_bookings):
    await visitor.click(_cb("day", arg=demo_bookings["day"]))
    for booking in ("stranger", "own", "fake"):
        await visitor.click(_cb("bk", demo_bookings[booking]))
    await visitor.click(_cb("bk_cancel", demo_bookings["stranger"]))  # екран підтвердження
    dmytro = await _master_id(engine, "Дмитро")
    await visitor.click(_cb("mst_hide", dmytro))  # попередження зі списком записів майстра
    assert visitor.last_text().startswith("⚠️ У майстра «Дмитро» є майбутні записи: 3")

    seen = _all_text(visitor)
    assert "+380501234593" not in seen  # телефон іншого відвідувача — ніде
    assert "+380 50 *** ** 93" in seen and "Ол***" in seen
    assert "+380671112233" in seen and "Іван" in seen  # свій запис — повністю
    assert "+380501112233" in seen and "Андрій" in seen  # вигаданий демо-клієнт — як є


async def test_masked_name_in_day_list_and_buttons(visitor, demo_bookings):
    await visitor.click(_cb("day", arg=demo_bookings["day"]))
    lines = visitor.last_text().splitlines()
    clients = [line.rsplit(" · ", 1)[-1] for line in lines if " · " in line]
    assert sorted(clients) == sorted(["Ол***", "Іван", "Андрій"])
    labels = [label for label, _ in visitor.buttons()]
    assert any("Ол***" in label for label in labels)
    assert not any("Олександр" in label.split("(")[0] for label in labels)  # у дужках — майстер


async def test_real_admin_in_demo_sees_everything(demo_dp, demo_bookings):
    owner = TelegramUser(demo_dp, user_id=ADMIN_ID, name="Власник")
    await owner.send("/admin")
    assert owner.last_text() == texts.ADMIN_MENU  # без демо-вступу
    await owner.click(_cb("bk", demo_bookings["stranger"]))
    assert "+380501234593" in owner.last_text() and "Олександр" in owner.last_text()


# --- Демо-адмінка: сповіщення не йдуть стороннім ------------------------------


def _messages_to_others(user: TelegramUser) -> list[SendMessage]:
    return [r for r in user.session.requests if isinstance(r, SendMessage) and r.chat_id != user.chat.id]


async def test_demo_owner_cancel_of_strangers_booking_notifies_nobody(visitor, engine, demo_bookings):
    await visitor.click(_cb("bk", demo_bookings["stranger"]))
    await visitor.click(visitor.button(texts.BTN_ADMIN_CANCEL))
    await visitor.click(visitor.button(texts.BTN_ADMIN_CANCEL_YES))

    assert texts.DEMO_CLIENT_NOT_NOTIFIED in visitor.last_text()
    assert _messages_to_others(visitor) == []  # ні клієнту, ні власнику, ні майстру
    async with create_session_factory(engine)() as session:
        status = (await session.get(Booking, demo_bookings["stranger"])).status
    assert status is BookingStatus.CANCELLED_BY_ADMIN  # сама дія в демо працює


async def test_demo_owner_cancel_of_fake_client_sends_nothing(visitor, demo_bookings):
    await visitor.click(_cb("bk_cancel_yes", demo_bookings["fake"]))
    assert _messages_to_others(visitor) == []


async def test_demo_owner_cancel_of_own_booking_notifies_only_himself(visitor, demo_bookings):
    await visitor.click(_cb("bk_cancel_yes", demo_bookings["own"]))
    assert texts.CLIENT_NOTIFIED in visitor.last_text()
    assert visitor.sent_to(VISITOR)[-1].startswith("😔 На жаль, ваш запис скасовано.")
    assert _messages_to_others(visitor) == []  # власник і майстер — ні


async def test_demo_owner_cannot_redirect_master_notifications(demo_dp, visitor, engine):
    """Відвідувач вписав майстру чужий Telegram ID — сповіщення туди не підуть."""
    await visitor.click(_cb("mst_edit", await _master_id(engine, "Дмитро"), "telegram_id"))
    await visitor.send(str(STRANGER))
    assert f"Telegram ID: {STRANGER}" in visitor.last_text()

    stranger = TelegramUser(demo_dp, user_id=STRANGER, name="Петро")
    await stranger.send(texts.BTN_BOOK)
    await stranger.click(stranger.button("Стрижка · 45 хв · 500 грн"))
    await stranger.click(stranger.button("Дмитро"))
    await stranger.click(stranger.buttons()[0][1])
    await stranger.click(stranger.slot_buttons()[0][1])
    await stranger.share_contact("+380501234593")
    await stranger.click(stranger.button(texts.BTN_CONFIRM))
    # Записувався сам Петро, тож «майстер з його ID» — це він; нікого іншого, крім власника
    assert {r.chat_id for r in _messages_to_others(stranger)} <= {ADMIN_ID}
    assert stranger.sent_to(MASTER_TG_ID) == []


async def test_demo_master_id_of_third_person_is_not_notified(demo_dp, visitor, engine):
    """Відвідувач вписав майстру ID третьої людини й записався сам — третій нічого не отримує."""
    await visitor.click(_cb("mst_edit", await _master_id(engine, "Дмитро"), "telegram_id"))
    await visitor.send(str(STRANGER))
    await _book(visitor, master="Дмитро")
    assert visitor.sent_to(STRANGER) == []
    assert {r.chat_id for r in _messages_to_others(visitor)} == {ADMIN_ID}


async def test_demo_master_with_visitors_own_id_is_notified(demo_dp, visitor, engine):
    """Якщо відвідувач вписав майстру СВІЙ ID — він бачить і сповіщення майстра."""
    await visitor.click(_cb("mst_edit", await _master_id(engine, "Дмитро"), "telegram_id"))
    await visitor.send(str(VISITOR))
    await _book(visitor, master="Дмитро")
    texts_to_me = visitor.sent_to(VISITOR)
    assert any(t.startswith("🆕 <b>Новий запис</b>") for t in texts_to_me)


# --- Відвідувач бачить обидві сторони -----------------------------------------


async def _book(user: TelegramUser, master: str = "Олександр",
                service: str = "Стрижка · 45 хв · 500 грн") -> None:
    await user.send(texts.BTN_BOOK)
    await user.click(user.button(service))
    await user.click(user.button(master))
    await user.click(user.buttons()[0][1])
    await user.click(user.slot_buttons()[0][1])
    await user.share_contact("+380671112233")
    await user.click(user.button(texts.BTN_CONFIRM))


async def test_visitor_sees_owner_notification(visitor):
    await _book(visitor)
    own_messages = [r.text for r in visitor._own() if isinstance(r, SendMessage)]
    owner_view = own_messages[-1]
    assert owner_view.startswith("👀 <b>Так це побачить власник салону:</b>")
    assert "🆕 <b>Новий запис</b>" in owner_view and "+380671112233" in owner_view
    # Справжній власник демо-бота отримує те саме сповіщення
    [to_admin] = visitor.sent_to(ADMIN_ID)
    assert owner_view.endswith(to_admin)


async def test_no_owner_view_outside_demo(user):
    await _book(user)
    assert not any(
        (r.text or "").startswith("👀") for r in user._own() if isinstance(r, SendMessage)
    )


# --- Нічне скидання -----------------------------------------------------------


async def test_reset_restores_everything(demo_dp, visitor, engine, business):
    factory = create_session_factory(engine)
    await reset_demo(factory, business)  # початковий стан демо
    await _book(visitor, master="Олександр", service="Чоловіча стрижка · 45 хв · 500 грн")

    # Відвідувач-«власник» змінює все, до чого має доступ
    async with factory() as session:
        svc = await session.scalar(select(Service).where(Service.name == "Чоловіча стрижка"))
        master = await session.scalar(select(Master).where(Master.name == "Дмитро"))
    await visitor.click(_cb("svc_edit", svc.id, "price"))
    await visitor.send("999")
    await visitor.click(_cb("svc_hide_force", svc.id))
    await visitor.click(_cb("mst_hide_force", master.id))
    await visitor.click(_cb("sched_day", master.id, "0"))
    await visitor.send("вихідний")
    if visitor.last_text().startswith("⚠️"):
        await visitor.click(visitor.button(texts.BTN_FORCE_SAVE))
    await visitor.click(_cb("off_add", master.id))
    await visitor.send(f"{_in(72).astimezone(business.booking.tz):%d.%m}")
    if visitor.last_text().startswith("⚠️"):
        await visitor.click(visitor.button(texts.BTN_FORCE_SAVE))
    async with factory() as session:  # зміни справді відбулися
        changed = await session.get(Service, svc.id)
        assert (changed.price, changed.is_active) == (999, False)
        assert await session.scalar(select(func.count()).select_from(TimeOff)) == 2

    await reset_demo(factory, business)

    async with factory() as session:
        services = (await session.scalars(select(Service))).all()
        assert {(s.name, s.price, s.is_active) for s in services} == {
            (name, price, True) for name, _, price in SERVICES.values()
        }
        dmytro = await session.scalar(select(Master).where(Master.name == "Дмитро"))
        assert dmytro.is_active
        visitor_client = await session.scalar(select(Client).where(Client.telegram_id == VISITOR))
        assert visitor_client is None  # записи й клієнти відвідувачів теж скинуті
        assert await session.scalar(select(func.count()).select_from(TimeOff)) == 1  # із seed


async def test_ensure_demo_data_only_fills_empty_db(engine, business):
    factory = create_session_factory(engine)
    await ensure_demo_data(factory, business)
    async with factory() as session:
        svc = await session.scalar(select(Service).limit(1))
        svc.name = "Змінена"
        await session.commit()
    await ensure_demo_data(factory, business)  # база не порожня — не чіпаємо
    async with factory() as session:
        assert await session.scalar(select(func.count()).where(Service.name == "Змінена")) == 1


def test_scheduler_has_nightly_reset_only_in_demo(engine, business):
    factory = create_session_factory(engine)
    demo = create_scheduler(None, factory, business, demo_mode=True)
    job = demo.get_job("demo_reset")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("4", "0")
    assert str(job.trigger.timezone) == "Europe/Kyiv"
    assert create_scheduler(None, factory, business, demo_mode=False).get_job("demo_reset") is None


async def test_real_admin_cancel_of_fake_client_does_not_try_to_send(demo_dp, demo_bookings):
    """Вигадані демо-клієнти не існують у Telegram — навіть справжній власник їм не пише."""
    owner = TelegramUser(demo_dp, user_id=ADMIN_ID, name="Власник")
    await owner.click(_cb("bk_cancel_yes", demo_bookings["fake"]))
    assert owner.sent_to(FAKE) == []
    assert "Не вдалося сповістити клієнта" in owner.last_text()



# --- Довільний текст у демо вимкнений -----------------------------------------


@pytest.mark.parametrize(
    ("action", "arg", "back"),
    [
        ("svc_add", "", "svcs"),
        ("svc_edit", "name", "svc"),
        ("mst_add", "", "masters"),
        ("mst_edit", "name", "mst"),
    ],
)
async def test_demo_owner_cannot_add_or_rename(visitor, engine, action, arg, back):
    service_id = await _service_id(engine, "Стрижка")
    master_id = await _master_id(engine, "Дмитро")
    item_id = {"svc": service_id, "mst": master_id}.get(back, 0)

    await visitor.click(_cb(action, item_id, arg))
    assert visitor.last_text() == texts.DEMO_NAMES_DISABLED
    assert visitor.buttons() == [(texts.BTN_BACK, _cb(back, item_id))]

    await visitor.send("Будь-який текст")  # не сприймається як назва
    async with create_session_factory(engine)() as session:
        assert set((await session.scalars(select(Service.name))).all()) == {"Стрижка", "Борода"}
        assert set((await session.scalars(select(Master.name))).all()) == {"Олександр", "Дмитро", "Максим"}


async def test_demo_owner_forged_input_state_is_rejected(demo_dp, visitor, engine):
    """Навіть якщо опинитися в стані введення назви — текст не збережеться."""
    state = demo_dp.fsm.resolve_context(visitor.bot, chat_id=VISITOR, user_id=VISITOR)
    await state.set_state(AdminStates.input)
    await state.set_data({"kind": "svc_name", "id": await _service_id(engine, "Стрижка")})
    await visitor.send("Реклама")
    assert visitor.last_text() == texts.DEMO_NAMES_DISABLED
    async with create_session_factory(engine)() as session:
        assert await session.scalar(select(func.count()).where(Service.name == "Реклама")) == 0


async def test_demo_owner_can_still_change_numbers_hide_and_schedule(visitor, engine):
    service_id = await _service_id(engine, "Стрижка")
    master_id = await _master_id(engine, "Дмитро")
    await visitor.click(_cb("svc_edit", service_id, "price"))
    await visitor.send("650")
    assert "650 грн" in visitor.last_text()
    await visitor.click(_cb("svc_edit", service_id, "duration_min"))
    await visitor.send("60")
    await visitor.click(_cb("svc_hide", service_id))
    await visitor.click(_cb("sched_day", master_id, "6"))
    await visitor.send("10:00-18:00")
    async with create_session_factory(engine)() as session:
        svc = await session.get(Service, service_id)
        assert (svc.price, svc.duration_min, svc.is_active) == (650, 60, False)


async def test_demo_owner_time_off_skips_free_text_reason(visitor, engine, business):
    master_id = await _master_id(engine, "Олександр")
    day = _in(72).astimezone(business.booking.tz)
    await visitor.click(_cb("off_add", master_id))
    await visitor.send(f"{day:%d.%m} 10:00-13:00")
    assert visitor.last_text().startswith("✅ Час заблоковано:")  # без кроку «Причина»
    async with create_session_factory(engine)() as session:
        [time_off] = (await session.scalars(select(TimeOff))).all()
    assert time_off.reason is None


async def test_real_admin_in_demo_can_add_and_rename(demo_dp, engine):
    owner = TelegramUser(demo_dp, user_id=ADMIN_ID, name="Власник")
    await owner.click(_cb("mst_add"))
    await owner.send("Віталій")
    assert owner.last_text().startswith("👤 <b>Віталій</b>")
    await owner.click(_cb("off_add", await _master_id(engine, "Віталій")))
    await owner.send(f"{_in(72).astimezone(demo_dp['business'].booking.tz):%d.%m}")
    assert owner.last_text() == texts.ASK_REASON  # причина для справжнього власника лишається


async def _service_id(engine, name: str) -> int:
    async with create_session_factory(engine)() as session:
        return await session.scalar(select(Service.id).where(Service.name == name))
