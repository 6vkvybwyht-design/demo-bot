"""Грумінг: запис із твариною, ціна й тривалість за розміром, сповіщення й нагадування."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import select

from bot import texts
from bot.config import PetsConfig, Settings, load_business_config, profile_path
from bot.db.models import Booking, Client, Master, Pet, Service
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb, PetCb
from bot.main import create_dispatcher
from bot.services.pets import Offer, offer_for, parse_pet_breed, parse_pet_name, PetInputError
from bot.services.reminders import send_due_reminders
from tests.conftest import ADMIN_ID, every_day
from tests.fake_telegram import TelegramUser

CLIENT = 777


@pytest.fixture
def groom():
    return load_business_config(profile_path("groom"))


@pytest.fixture
async def groom_dp(engine, groom):
    factory = create_session_factory(engine)
    async with factory() as session:
        complex_ = Service(name="Комплексний грумінг", duration_min=90, price=700)
        bath = Service(name="Купання", duration_min=30, price=300)
        claws = Service(name="Підстригання кігтів", duration_min=15, price=150)
        extra = Service(name="Спа для лап", duration_min=40, price=500)  # немає в прайсі
        session.add_all([
            complex_, bath, claws, extra,
            Master(name="Наталія", services=[complex_, bath, claws, extra], working_hours=every_day()),
            Master(name="Андрій", services=[complex_, bath], working_hours=every_day()),
        ])
        await session.commit()
    settings = Settings(_env_file=None, bot_token="42:TEST", admin_ids=[ADMIN_ID])
    return create_dispatcher(settings, groom, factory)


@pytest.fixture
def owner_of_pet(groom_dp):
    return TelegramUser(groom_dp, user_id=CLIENT, name="Іван")


async def _add_pet(user: TelegramUser, name="Рекс", species="🐕 Собака", breed="лабрадор",
                   size="Великий · понад 25 кг") -> None:
    await user.send(name)
    await user.click(user.button(species))
    await user.send(breed)
    await user.click(user.button(size))


async def _book(user: TelegramUser, service_label: str, master="Наталія", date_index=0) -> None:
    await user.click(user.button(service_label))
    await user.click(user.button(master))
    await user.click(user.buttons()[date_index][1])
    await user.click(user.slot_buttons()[0][1])


async def _bookings(engine) -> list[Booking]:
    async with create_session_factory(engine)() as session:
        return list((await session.scalars(select(Booking))).all())


# --- Прайс за розміром --------------------------------------------------------


def test_offer_for(groom):
    complex_ = Service(name="Комплексний грумінг", duration_min=90, price=700)
    extra = Service(name="Спа для лап", duration_min=40, price=500)
    assert offer_for(complex_, "mini", groom.pets) == Offer(90, 700)
    assert offer_for(complex_, "large", groom.pets) == Offer(180, 1600)
    assert offer_for(extra, "large", groom.pets) == Offer(40, 500)  # немає в прайсі — базові
    assert offer_for(complex_, None, groom.pets) == Offer(90, 700)
    assert offer_for(complex_, "large", None) == Offer(90, 700)  # профіль без тварин

    partial = PetsConfig.model_validate({
        "species": [{"key": "dog", "label": "Собака"}],
        "sizes": [{"key": "mini", "label": "Міні"}, {"key": "large", "label": "Великий"}],
        "prices": {"Комплексний грумінг": {"mini": {"duration_min": 60, "price": 500}}},
    })
    assert offer_for(complex_, "large", partial) is None  # для великих — недоступна


def test_groom_price_table(groom):
    prices = groom.pets.prices
    assert set(prices) == {s.name for s in groom.demo.services}
    for row in prices.values():
        assert set(row) == {"mini", "small", "medium", "large"}
        durations = [row[s].duration_min for s in ("mini", "small", "medium", "large")]
        assert durations == sorted(durations)  # більша тварина — не швидше
    assert groom.reminders.before_24h and not groom.reminders.before_2h


def test_pet_input_validation():
    assert parse_pet_name("  Рекс  ") == "Рекс"
    assert parse_pet_breed("джек-рассел  тер'єр") == "джек-рассел тер'єр"
    for bad in ("", "   ", "х" * 31):
        with pytest.raises(PetInputError):
            parse_pet_name(bad)
    with pytest.raises(PetInputError):
        parse_pet_breed("x" * 51)


# --- Запис із новою твариною --------------------------------------------------


async def test_full_booking_with_new_pet(owner_of_pet, engine):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.ASK_PET_NAME  # збережених тварин ще немає

    await user.send("Рекс")
    assert user.last_text() == texts.ASK_PET_SPECIES.format(name="Рекс")
    assert [label for label, _ in user.buttons()] == ["🐕 Собака", "🐈 Кіт", texts.BTN_BACK]
    await user.click(user.button("🐕 Собака"))
    assert user.last_text() == texts.ASK_PET_BREED
    await user.send("лабрадор")
    assert user.last_text() == texts.ASK_PET_SIZE
    assert [label for label, _ in user.buttons()] == [
        "Міні · до 5 кг", "Малий · 5–10 кг", "Середній · 10–25 кг", "Великий · понад 25 кг", texts.BTN_BACK,
    ]
    await user.click(user.button("Великий · понад 25 кг"))

    # Ціни й тривалість — для великого пса; тварина в підсумку
    assert user.last_text().startswith("🧾 <b>Рекс</b>")
    assert [label for label, _ in user.buttons()] == [
        "Комплексний грумінг · 180 хв · 1600 грн",
        "Купання · 75 хв · 700 грн",
        "Підстригання кігтів · 20 хв · 250 грн",
        "Спа для лап · 40 хв · 500 грн",
        texts.BTN_BACK,
    ]
    await _book(user, "Комплексний грумінг · 180 хв · 1600 грн")
    await user.share_contact("+380501234567")
    confirm = user.last_text()
    assert "🐾 Тварина: <b>Рекс</b> (собака, лабрадор, великий)" in confirm
    assert "⏱ Тривалість: 180 хв" in confirm and "💵 Вартість: 1600 грн" in confirm

    await user.click(user.button(texts.BTN_CONFIRM))
    assert "Ви записані" in user.last_text() and "Рекс" in user.last_text()
    [booking] = await _bookings(engine)
    assert booking.end_dt - booking.start_dt == timedelta(minutes=180)
    assert booking.price == 1600
    async with create_session_factory(engine)() as session:
        pet = await session.get(Pet, booking.pet_id)
        owner = await session.get(Client, pet.client_id)
    assert (pet.name, pet.species, pet.breed, pet.size) == ("Рекс", "dog", "лабрадор", "large")
    assert owner.telegram_id == CLIENT


async def test_admin_gets_all_pet_data(owner_of_pet):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await _add_pet(user, name="Мурка", species="🐈 Кіт", breed="британська", size="Малий · 5–10 кг")
    await _book(user, "Купання · 45 хв · 400 грн")
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_CONFIRM))

    [to_admin] = user.sent_to(ADMIN_ID)
    assert to_admin.startswith("🆕 <b>Новий запис</b>")
    for part in ("🐾 Тварина: <b>Мурка</b> (кіт, британська, малий)", "Купання",
                 "⏱ Тривалість: 45 хв", "💵 Вартість: 400 грн", "Іван", "+380501234567"):
        assert part in to_admin, part


async def test_saved_pet_is_offered_next_time(owner_of_pet, engine):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await _add_pet(user, name="Лакі", species="🐕 Собака", breed="шпіц", size="Міні · до 5 кг")
    await user.send(texts.BTN_BOOK)  # передумали — починаємо заново
    assert user.last_text() == texts.CHOOSE_PET
    assert [label for label, _ in user.buttons()] == ["🐾 Лакі · Міні", texts.BTN_NEW_PET, texts.BTN_BACK]
    await user.click(user.button("🐾 Лакі · Міні"))
    assert "Комплексний грумінг · 90 хв · 700 грн" in [label for label, _ in user.buttons()]

    await user.send(texts.BTN_BOOK)
    await user.click(user.button(texts.BTN_NEW_PET))
    assert user.last_text() == texts.ASK_PET_NAME
    async with create_session_factory(engine)() as session:
        assert len((await session.scalars(select(Pet))).all()) == 1


async def test_foreign_pet_is_not_usable(groom_dp, engine):
    stranger = TelegramUser(groom_dp, user_id=999)
    await stranger.send(texts.BTN_BOOK)
    await _add_pet(stranger)
    async with create_session_factory(engine)() as session:
        pet_id = await session.scalar(select(Pet.id))

    user = TelegramUser(groom_dp, user_id=CLIENT)
    await user.send(texts.BTN_BOOK)
    await _add_pet(user, name="Бобік")
    await user.send(texts.BTN_BOOK)
    await user.click(PetCb(pet_id=pet_id).pack())  # підроблена кнопка з чужою твариною
    assert user.last_text() == texts.CHOOSE_PET
    assert "Рекс" not in " ".join(label for label, _ in user.buttons())


async def test_pet_steps_back_and_validation(owner_of_pet):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await user.send("х" * 31)
    assert user.last_text() == texts.INVALID_PET_NAME
    await user.send("Рекс")
    await user.click(user.button("🐕 Собака"))
    await user.click(user.button(texts.BTN_BACK))  # з породи — до виду
    assert user.last_text() == texts.ASK_PET_SPECIES.format(name="Рекс")
    await user.click(user.button(texts.BTN_BACK))  # з виду — до клички
    assert user.last_text() == texts.ASK_PET_NAME
    await user.click(user.button(texts.BTN_BACK))  # збережених тварин немає — у меню
    assert user.last_text() == texts.BACK_TO_MENU


async def test_menu_button_is_not_taken_as_pet_name(owner_of_pet, engine):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await user.send(texts.BTN_MY_BOOKINGS)
    assert user.last_text() == texts.MY_BOOKINGS_EMPTY
    async with create_session_factory(engine)() as session:
        assert (await session.scalars(select(Pet))).all() == []


async def test_barber_has_no_pet_step(user):
    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.CHOOSE_SERVICE


# --- Тривалість за розміром у слотах і перенесенні ----------------------------


async def test_reschedule_keeps_pet_duration_and_price(owner_of_pet, engine):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await _add_pet(user)
    await _book(user, "Комплексний грумінг · 180 хв · 1600 грн", date_index=-2)
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_CONFIRM))

    await user.send(texts.BTN_MY_BOOKINGS)
    assert "🐾 Тварина: <b>Рекс</b>" in user.last_text()
    await user.click(user.button(texts.BTN_MOVE_BOOKING))
    await user.click(user.buttons()[-2][1])
    # Останній слот дня: 23:45 − 180 хв = 20:45, а не 22:15 (як для базових 90 хв)
    assert user.slot_buttons()[-1][0] == "20:45"
    await user.click(user.slot_buttons()[-1][1])
    await user.click(user.button(texts.BTN_MOVE_CONFIRM))
    [booking] = await _bookings(engine)
    assert booking.end_dt - booking.start_dt == timedelta(minutes=180)
    assert booking.price == 1600


async def test_slots_use_pet_duration(owner_of_pet):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await _add_pet(user, size="Міні · до 5 кг")
    await user.click(user.button("Комплексний грумінг · 90 хв · 700 грн"))
    await user.click(user.button("Наталія"))
    await user.click(user.buttons()[-2][1])
    assert user.slot_buttons()[-1][0] == "22:15"  # 23:45 − 90 хв


# --- Нагадування й адмінка ----------------------------------------------------


async def test_reminder_only_day_before(groom_dp, engine, groom):
    factory = create_session_factory(engine)
    visit = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    async with factory() as session:
        client = Client(telegram_id=CLIENT, name="Іван", phone="+380501234567")
        pet = Pet(client=client, name="Рекс", species="dog", breed="лабрадор", size="large")
        session.add(pet)
        await session.flush()
        session.add(Booking(
            client=client, pet=pet, price=1600, start_dt=visit, end_dt=visit + timedelta(minutes=180),
            master=await session.scalar(select(Master).where(Master.name == "Наталія")),
            service=await session.scalar(select(Service).where(Service.name == "Комплексний грумінг")),
            created_at=visit - timedelta(days=5),
        ))
        await session.commit()

    user = TelegramUser(groom_dp, user_id=CLIENT)
    sent = []
    for now in (visit - timedelta(hours=23), visit - timedelta(hours=2), visit - timedelta(hours=1)):
        before = len(user.session.requests)
        await send_due_reminders(user.bot, factory, groom, now=now)
        sent += [r.text for r in user.session.requests[before:] if isinstance(r, SendMessage)]
    assert len(sent) == 1  # лише за день; за 2 години — ні
    assert "🐾 Тварина: <b>Рекс</b> (собака, лабрадор, великий)" in sent[0]
    assert "💵 Вартість: 1600 грн" in sent[0]


async def test_admin_sees_pet_and_price_table(groom_dp, owner_of_pet, engine, groom):
    user = owner_of_pet
    await user.send(texts.BTN_BOOK)
    await _add_pet(user)
    await _book(user, "Купання · 75 хв · 700 грн")
    await user.share_contact("+380501234567")
    await user.click(user.button(texts.BTN_CONFIRM))
    [booking] = await _bookings(engine)

    owner = TelegramUser(groom_dp, user_id=ADMIN_ID)
    day = booking.start_dt.astimezone(groom.booking.tz).date().isoformat()
    await owner.click(AdminCb(action="day", arg=day).pack())
    assert "Іван · 🐾 Рекс" in owner.last_text()
    await owner.click(AdminCb(action="bk", id=booking.id).pack())
    assert "🐾 Тварина: <b>Рекс</b> (собака, лабрадор, великий)" in owner.last_text()
    assert "💵 Вартість: 700 грн" in owner.last_text()

    async with create_session_factory(engine)() as session:
        bath = await session.scalar(select(Service.id).where(Service.name == "Купання"))
        extra = await session.scalar(select(Service.id).where(Service.name == "Спа для лап"))
    await owner.click(AdminCb(action="svc", id=bath).pack())
    assert "• Великий: 75 хв · 700 грн" in owner.last_text()
    await owner.click(AdminCb(action="svc", id=extra).pack())
    assert "немає в прайсі профілю" in owner.last_text()


async def test_groom_texts(groom_dp):
    assert texts.BTN_ADMIN_SERVICES == "🐩 Послуги"
