"""Грумінг: ціна й тривалість за видом, породою й вагою; додаткові послуги; ціна «від»."""

from datetime import timedelta

import pytest
from aiogram.methods import SendMessage
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot import texts
from bot.config import PetsConfig, Settings, load_business_config, profile_path
from bot.db.models import Booking, Client, Master, Pet, Service
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AddonCb, AdminCb, PetCb
from bot.main import create_dispatcher
from bot.services.pets import Offer, PetInputError, offer_for, parse_pet_name, total_offer
from bot.services.reminders import send_due_reminders
from tests.conftest import ADMIN_ID, every_day
from tests.fake_telegram import TelegramUser

CLIENT = 777
NOTE = texts.PRICE_FROM_NOTE.strip()


@pytest.fixture
def groom():
    return load_business_config(profile_path("groom"))


@pytest.fixture
def pets(groom) -> PetsConfig:
    return groom.pets


# --- Прайс: ціни з прайс-листа салону -----------------------------------------


def _price(pets: PetsConfig, service: str, path: str) -> tuple[int, bool] | None:
    entry = pets.price_for(service, path)
    return (entry.price, entry.price_from) if entry else None


COMPLEX_DOG = {
    "dog/mixed/4": (900, True), "dog/mixed/6": (1000, True), "dog/mixed/12": (1100, True),
    "dog/mixed/16": (1200, True), "dog/mixed/16+": (1300, True),
    "dog/york": (1100, False), "dog/spitz/4": (1200, False), "dog/spitz/6": (1300, False),
    "dog/spitz/6+": (1400, False), "dog/maltese": (1200, False), "dog/shihtzu": (1200, False),
    "dog/toypoodle/4": (1300, False), "dog/toypoodle/6": (1400, False), "dog/cockapoo": (1450, False),
    "dog/poodle/10": (1600, False), "dog/poodle/14": (1900, False), "dog/poodle/14+": (2000, True),
    "dog/bichon": (1400, True), "dog/cocker": (1300, False), "dog/westie": (1400, False),
    "dog/schnauzer": (1300, False),
}
HYGIENE_DOG = {
    "dog/mixed/4": (700, True), "dog/mixed/6": (750, True), "dog/mixed/12": (900, True),
    "dog/mixed/16": (1000, True), "dog/mixed/16+": (1100, True),
    "dog/york": (900, False), "dog/spitz/4": (1000, False), "dog/spitz/6": (1100, False),
    "dog/spitz/6+": (1200, False), "dog/maltese": (1000, False), "dog/shihtzu": (1000, False),
    "dog/toypoodle/4": (1100, False), "dog/toypoodle/6": (1100, False), "dog/cockapoo": (1150, False),
    "dog/poodle/10": (1500, False), "dog/poodle/14": (1700, False), "dog/poodle/14+": (1800, True),
    "dog/bichon": (1100, True), "dog/cocker": (1000, False), "dog/westie": (1100, False),
    "dog/schnauzer": (1000, False),
}
EXPRESS_DOG = {
    "dog/mixed/4": (700, False), "dog/mixed/6": (900, True), "dog/mixed/12": (1100, True),
    "dog/mixed/16": (1200, True), "dog/mixed/16+": (1400, True),
}
CATS = {
    "Вичісування + кігті": {"cat/mixed/4": (900, False), "cat/mixed/4+": (1000, False),
                            "cat/mainecoon": (1100, False)},
    "Комплексний догляд": {"cat/mixed/4": (1200, False), "cat/mixed/4+": (1300, False),
                           "cat/mainecoon": (1400, False)},
    "Купання": {"cat/mixed/4": (800, False), "cat/mixed/4+": (900, False)},
    "Обрізання кігтів": {"cat/mixed/4": (200, False), "cat/mixed/4+": (200, False),
                         "cat/mainecoon": (200, False)},
}
ADDONS = {
    "Чищення зубів": (150, False), "Чищення вух": (100, False),
    "Підрізання та запилювання кігтів": (200, False), "Чищення параанальних залоз": (200, False),
    "Вичісування / видалення ковтунів": (100, True),
}


@pytest.mark.parametrize(
    ("service", "table"),
    [("Комплексний догляд", COMPLEX_DOG), ("Гігієнічний догляд", HYGIENE_DOG),
     ("Експрес-линька", EXPRESS_DOG), *CATS.items()],
)
def test_prices_match_price_list(pets, service, table):
    for path, expected in table.items():
        assert _price(pets, service, path) == expected, path


def test_rabbit_and_addons(pets):
    assert _price(pets, "Вичісування", "rabbit") == (600, True)
    for service, expected in ADDONS.items():
        for path in ("dog/york", "dog/mixed/16+", "cat/mainecoon", "cat/mixed/4"):
            assert _price(pets, service, path) == expected, (service, path)
            assert pets.is_addon(service)
        assert _price(pets, service, "rabbit") is None  # лише для котів і собак
    # Маска: 200 до 8 кг, 300 від 8 кг; категорії довкола 8 кг — «від 200»
    assert _price(pets, "Маска для шерсті", "dog/york") == (200, False)
    assert _price(pets, "Маска для шерсті", "dog/mixed/12") == (200, True)
    assert _price(pets, "Маска для шерсті", "dog/mixed/16+") == (300, False)
    assert _price(pets, "Маска для шерсті", "dog/cocker") == (300, False)
    assert _price(pets, "Маска для шерсті", "cat/mixed/4") == (200, False)


def test_services_unavailable_for_category(pets):
    # Експрес-линька — лише метиси за вагою; котячі послуги — не собакам; кролик — лише вичісування
    assert _price(pets, "Експрес-линька", "dog/spitz/4") is None
    assert _price(pets, "Купання", "dog/york") is None
    assert _price(pets, "Гігієнічний догляд", "cat/mixed/4") is None
    for service in pets.services:
        if service != "Вичісування":
            assert _price(pets, service, "rabbit") is None, service


def test_durations(pets):
    def duration(service, path):
        return pets.price_for(service, path).duration_min

    # Комплексний: до 6 кг — 2 год, до 16 кг — 2,5 год, від 16 кг — 3 год
    assert [duration("Комплексний догляд", f"dog/mixed/{w}") for w in ("4", "6", "12", "16", "16+")] == [
        120, 120, 150, 150, 180
    ]
    for path in pets.categories():
        if path.startswith("dog/"):
            assert 60 <= duration("Гігієнічний догляд", path) <= 90
        if path.startswith("cat/"):
            assert duration("Вичісування + кігті", path) == 60
            assert duration("Комплексний догляд", path) == 120
            assert duration("Купання", path) == 60
    assert [duration("Експрес-линька", f"dog/mixed/{w}") for w in ("4", "16+")] == [90, 180]
    for service in ADDONS:
        assert duration(service, "dog/york") == 15


def test_most_specific_price_wins(pets):
    # dog/toypoodle задає гігієнічний для обох ваг; dog — додаткові послуги для всіх собак
    assert pets.price_for("Гігієнічний догляд", "dog/toypoodle/6").price == 1100
    assert pets.price_for("Маска для шерсті", "dog/poodle/14").price == 300
    assert pets.price_for("Маска для шерсті", "dog/maltese").price == 200  # із ключа «dog»


def test_categories_and_descriptions(pets):
    cats = pets.categories()
    assert len(cats) == 25
    assert {"dog/spitz/4", "dog/mixed/16+", "cat/mainecoon", "cat/mixed/4+", "rabbit"} <= set(cats)
    assert pets.describe("dog/spitz/4") == "собака, шпіц, до 4 кг"
    assert pets.describe("dog/mixed/12", with_species=False) == "інша порода / метис, до 12 кг"
    assert pets.describe("rabbit", with_species=False) == "кролик"
    with pytest.raises(ValueError):
        pets.category_path("dog", "spitz")  # шпіцу потрібна вага


def test_price_key_validation(groom):
    data = groom.pets.model_dump(by_alias=True)
    data["services"]["Купання"]["prices"]["cat/sphynx"] = {"price": 1, "duration_min": 10}
    with pytest.raises(ValidationError, match="невідомі категорії: cat/sphynx"):
        PetsConfig.model_validate(data)


def test_offer_and_total():
    complex_ = Service(name="X", duration_min=90, price=700)
    assert offer_for(complex_, "dog/york", None) == Offer(90, 700)
    total = total_offer([Offer(120, 1200), Offer(15, 150), Offer(15, 100, price_from=True)])
    assert total == Offer(150, 1450, price_from=True)


def test_pet_name_validation():
    assert parse_pet_name("  Лакі  ") == "Лакі"
    for bad in ("", "   ", "х" * 31):
        with pytest.raises(PetInputError):
            parse_pet_name(bad)


# --- Сценарій у Telegram ------------------------------------------------------


@pytest.fixture
async def groom_dp(engine, groom):
    factory = create_session_factory(engine)
    async with factory() as session:
        main = [Service(name=n, duration_min=60, price=500) for n in (
            "Комплексний догляд", "Гігієнічний догляд", "Експрес-линька", "Купання", "Вичісування",
        )]
        addons = [Service(name=n, duration_min=15, price=100) for n in (
            "Чищення зубів", "Чищення вух", "Маска для шерсті",
        )]
        extra = Service(name="Спа для лап", duration_min=40, price=500)  # немає в прайсі
        session.add_all([
            *main, *addons, extra,
            Master(name="Наталія", services=[*main, extra], working_hours=every_day()),
            Master(name="Андрій", services=main[:2], working_hours=every_day()),
        ])
        await session.commit()
    settings = Settings(_env_file=None, bot_token="42:TEST", admin_ids=[ADMIN_ID])
    return create_dispatcher(settings, groom, factory)


@pytest.fixture
def client_user(groom_dp):
    return TelegramUser(groom_dp, user_id=CLIENT, name="Іван")


def labels(user: TelegramUser) -> list[str]:
    return [label for label, _ in user.buttons()]


async def _new_pet(user: TelegramUser, name: str, species: str, breed: str | None = None,
                   weight: str | None = None) -> None:
    await user.send(texts.BTN_BOOK)
    if user.last_text() == texts.CHOOSE_PET:
        await user.click(user.button(texts.BTN_NEW_PET))
    await user.send(name)
    await user.click(user.button(species))
    if breed:
        await user.click(user.button(breed))
    if weight:
        await user.click(user.button(weight))


async def _finish(user: TelegramUser, master="Наталія", date_index=0) -> None:
    await user.click(user.button(master))
    await user.click(user.buttons()[date_index][1])
    await user.click(user.slot_buttons()[0][1])
    if user.last_text() == texts.ASK_PHONE:
        await user.share_contact("+380501234567")


async def _booking(engine) -> Booking:
    async with create_session_factory(engine)() as session:
        return await session.scalar(
            select(Booking).options(selectinload(Booking.addons), selectinload(Booking.pet))
        )


async def test_breed_with_weight_and_addons(client_user, engine):
    user = client_user
    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.ASK_PET_NAME
    await user.send("Лакі")
    assert labels(user) == ["🐕 Собака", "🐈 Кіт", "🐇 Кролик", texts.BTN_BACK]
    await user.click(user.button("🐕 Собака"))
    assert user.last_text() == texts.ASK_PET_BREED
    assert "Шпіц" in labels(user) and labels(user)[-2:] == ["Інша порода / метис", texts.BTN_BACK]
    await user.click(user.button("Шпіц"))
    assert user.last_text() == texts.ASK_PET_WEIGHT.format(name="Лакі")
    assert labels(user) == ["до 4 кг", "до 6 кг", "від 6 кг", texts.BTN_BACK]
    await user.click(user.button("до 4 кг"))

    assert user.last_text().startswith("🧾 <b>Лакі</b>")
    assert labels(user) == [
        "Комплексний догляд · 120 хв · 1200 грн",
        "Гігієнічний догляд · 60 хв · 1000 грн",
        "Спа для лап · 40 хв · 500 грн",  # немає в прайсі — для всіх
        texts.BTN_BACK,
    ]  # експрес-линька — лише метисам; додаткові — окремим кроком

    await user.click(user.button("Комплексний догляд · 120 хв · 1200 грн"))
    assert labels(user) == [
        "▫️ Чищення зубів · 15 хв · 150 грн", "▫️ Чищення вух · 15 хв · 100 грн",
        "▫️ Маска для шерсті · 15 хв · 200 грн", texts.BTN_ADDONS_NEXT, texts.BTN_BACK,
    ]
    await user.click(user.button("▫️ Чищення зубів · 15 хв · 150 грн"))
    await user.click(user.button("▫️ Маска для шерсті · 15 хв · 200 грн"))
    await user.click(user.button("✅ Маска для шерсті · 15 хв · 200 грн"))  # передумали
    await user.click(user.button("▫️ Чищення вух · 15 хв · 100 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user)

    confirm = user.last_text()
    assert "🐾 Тварина: <b>Лакі</b> (собака, шпіц, до 4 кг)" in confirm
    assert "⏱ Тривалість: 150 хв" in confirm and "💵 Вартість: 1450 грн" in confirm
    assert "➕ Додатково: Чищення зубів, Чищення вух" in confirm
    assert NOTE not in confirm

    await user.click(user.button(texts.BTN_CONFIRM))
    booking = await _booking(engine)
    assert booking.end_dt - booking.start_dt == timedelta(minutes=150)
    assert (booking.price, booking.price_from) == (1450, False)
    assert sorted((a.price, a.duration_min) for a in booking.addons) == [(100, 15), (150, 15)]
    assert booking.pet.category == "dog/spitz/4"

    [to_admin] = user.sent_to(ADMIN_ID)
    for part in ("🆕 <b>Новий запис</b>", "🐾 Тварина: <b>Лакі</b> (собака, шпіц, до 4 кг)",
                 "Комплексний догляд", "➕ Додатково: Чищення зубів, Чищення вух",
                 "💵 Вартість: 1450 грн", "Іван", "+380501234567"):
        assert part in to_admin, part


async def test_mixed_breed_price_from(client_user, engine):
    user = client_user
    await _new_pet(user, "Рекс", "🐕 Собака", "Інша порода / метис", "до 12 кг")
    assert "Комплексний догляд · 150 хв · від 1100 грн" in labels(user)
    assert "Експрес-линька · 150 хв · від 1100 грн" in labels(user)
    await user.click(user.button("Комплексний догляд · 150 хв · від 1100 грн"))
    assert "▫️ Маска для шерсті · 15 хв · від 200 грн" in labels(user)
    await user.click(user.button(texts.BTN_ADDONS_NEXT))  # без додаткових
    await _finish(user)

    confirm = user.last_text()
    assert "💵 Вартість: від 1100 грн" in confirm and confirm.endswith(NOTE)
    assert "собака, інша порода / метис, до 12 кг" in confirm
    await user.click(user.button(texts.BTN_CONFIRM))
    assert NOTE in user.last_text()
    assert NOTE in user.sent_to(ADMIN_ID)[0]
    booking = await _booking(engine)
    assert (booking.price, booking.price_from, booking.addons) == (1100, True, [])


async def test_any_from_addon_makes_total_from(client_user):
    user = client_user
    await _new_pet(user, "Бусинка", "🐕 Собака", "Йоркширський тер'єр")  # вагу не питаємо
    await user.click(user.button("Комплексний догляд · 120 хв · 1100 грн"))
    await user.click(user.button("▫️ Маска для шерсті · 15 хв · 200 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user)
    assert "💵 Вартість: 1300 грн" in user.last_text() and NOTE not in user.last_text()


async def test_cat_mainecoon(client_user):
    user = client_user
    await _new_pet(user, "Барон", "🐈 Кіт")
    assert labels(user) == ["Мейн-кун", "Інша порода", texts.BTN_BACK]
    await user.click(user.button("Мейн-кун"))
    assert labels(user) == [
        "Комплексний догляд · 120 хв · 1400 грн",
        "Купання · 60 хв · 900 грн",
        "Спа для лап · 40 хв · 500 грн",
        texts.BTN_BACK,
    ]


async def test_cat_by_weight(client_user):
    user = client_user
    await _new_pet(user, "Мурка", "🐈 Кіт", "Інша порода", "до 4 кг")
    assert "Купання · 60 хв · 800 грн" in labels(user)
    assert not any("Гігієнічний" in label for label in labels(user))


async def test_rabbit_has_no_breed_weight_or_addons(client_user, engine):
    user = client_user
    await _new_pet(user, "Пухнастик", "🐇 Кролик")
    assert labels(user) == ["Вичісування · 60 хв · від 600 грн", "Спа для лап · 40 хв · 500 грн", texts.BTN_BACK]
    await user.click(user.button("Вичісування · 60 хв · від 600 грн"))
    assert user.last_text().endswith(texts.CHOOSE_MASTER)  # додаткових для кролика немає
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text().endswith(texts.CHOOSE_SERVICE)  # назад — одразу до послуг
    async with create_session_factory(engine)() as session:
        pet = await session.scalar(select(Pet))
    assert (pet.category, pet.breed) == ("rabbit", "кролик")


async def test_back_through_pet_steps(client_user):
    user = client_user
    await _new_pet(user, "Граф", "🐕 Собака", "Пудель")
    assert user.last_text() == texts.ASK_PET_WEIGHT.format(name="Граф")
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text() == texts.ASK_PET_BREED
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text() == texts.ASK_PET_SPECIES.format(name="Граф")
    await user.click(user.button(texts.BTN_BACK))
    assert user.last_text() == texts.ASK_PET_NAME


async def test_back_from_master_returns_to_addons(client_user):
    user = client_user
    await _new_pet(user, "Бусинка", "🐕 Собака", "Йоркширський тер'єр")
    await user.click(user.button("Гігієнічний догляд · 60 хв · 900 грн"))
    await user.click(user.button("▫️ Чищення вух · 15 хв · 100 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await user.click(user.button(texts.BTN_BACK))
    assert "✅ Чищення вух · 15 хв · 100 грн" in labels(user)  # вибір збережено


async def test_saved_pet_and_forged_buttons(groom_dp, client_user, engine):
    user = client_user
    await _new_pet(user, "Лакі", "🐕 Собака", "Шпіц", "до 4 кг")
    await user.send(texts.BTN_BOOK)
    assert labels(user) == ["🐾 Лакі · шпіц, до 4 кг", texts.BTN_NEW_PET, texts.BTN_BACK]
    await user.click(user.button("🐾 Лакі · шпіц, до 4 кг"))
    await user.click(user.button("Комплексний догляд · 120 хв · 1200 грн"))

    async with create_session_factory(engine)() as session:
        # Основна послуга, доступна шпіцу: як «додаткова» її не можна додати
        hygiene = await session.scalar(select(Service.id).where(Service.name == "Гігієнічний догляд"))
        lucky_id = await session.scalar(select(Pet.id))
        # Тварина зі старим «розміром» (до зміни прайсу) — у списку не показується
        owner = await session.scalar(select(Client).where(Client.telegram_id == CLIENT))
        session.add(Pet(client_id=owner.id, name="Старий", species="dog", breed="?", category="large"))
        await session.commit()
    await user.click(AddonCb(service_id=hygiene).pack())  # підроблена кнопка
    assert "✅" not in " ".join(labels(user))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user)
    await user.click(user.button(texts.BTN_CONFIRM))
    booking = await _booking(engine)
    assert (booking.price, booking.addons) == (1200, [])  # підроблений вибір не потрапив у запис

    stranger = TelegramUser(groom_dp, user_id=999)
    await _new_pet(stranger, "Рекс", "🐕 Собака", "Інша порода / метис", "від 16 кг")
    await stranger.send(texts.BTN_BOOK)
    await stranger.click(PetCb(pet_id=lucky_id).pack())  # чужа тварина
    assert stranger.last_text() == texts.CHOOSE_PET

    await user.send(texts.BTN_BOOK)
    assert "Старий" not in " ".join(labels(user))


async def test_reschedule_keeps_total_duration_price_and_addons(client_user, engine):
    user = client_user
    await _new_pet(user, "Лакі", "🐕 Собака", "Шпіц", "до 4 кг")
    await user.click(user.button("Комплексний догляд · 120 хв · 1200 грн"))
    await user.click(user.button("▫️ Чищення зубів · 15 хв · 150 грн"))
    await user.click(user.button("▫️ Чищення вух · 15 хв · 100 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user, date_index=-2)
    await user.click(user.button(texts.BTN_CONFIRM))

    await user.send(texts.BTN_MY_BOOKINGS)
    assert "➕ Додатково: Чищення зубів, Чищення вух" in user.last_text()
    await user.click(user.button(texts.BTN_MOVE_BOOKING))
    await user.click(user.buttons()[-2][1])
    assert user.slot_buttons()[-1][0] == "21:15"  # 23:45 − 150 хв
    await user.click(user.slot_buttons()[-1][1])
    await user.click(user.button(texts.BTN_MOVE_CONFIRM))
    booking = await _booking(engine)
    assert booking.end_dt - booking.start_dt == timedelta(minutes=150)
    assert booking.price == 1450 and len(booking.addons) == 2


async def test_barber_has_no_pet_step(user):
    await user.send(texts.BTN_BOOK)
    assert user.last_text() == texts.CHOOSE_SERVICE


# --- Нагадування й адмінка ----------------------------------------------------


async def test_reminder_only_day_before(groom_dp, engine, groom, client_user):
    user = client_user
    await _new_pet(user, "Рекс", "🐕 Собака", "Інша порода / метис", "від 16 кг")
    await user.click(user.button("Комплексний догляд · 180 хв · від 1300 грн"))
    await user.click(user.button("▫️ Чищення вух · 15 хв · 100 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user, date_index=-2)
    await user.click(user.button(texts.BTN_CONFIRM))
    visit = (await _booking(engine)).start_dt

    factory = create_session_factory(engine)
    sent = []
    for now in (visit - timedelta(hours=23), visit - timedelta(hours=2), visit - timedelta(hours=1)):
        before = len(user.session.requests)
        await send_due_reminders(user.bot, factory, groom, now=now)
        sent += [r.text for r in user.session.requests[before:] if isinstance(r, SendMessage)]
    assert len(sent) == 1  # лише за день
    for part in ("🐾 Тварина: <b>Рекс</b>", "➕ Додатково: Чищення вух", "від 1400 грн", NOTE):
        assert part in sent[0], part


async def test_admin_sees_pet_addons_and_price_table(groom_dp, client_user, engine, groom):
    user = client_user
    await _new_pet(user, "Лакі", "🐕 Собака", "Шпіц", "до 4 кг")
    await user.click(user.button("Гігієнічний догляд · 60 хв · 1000 грн"))
    await user.click(user.button("▫️ Чищення зубів · 15 хв · 150 грн"))
    await user.click(user.button(texts.BTN_ADDONS_NEXT))
    await _finish(user)
    await user.click(user.button(texts.BTN_CONFIRM))
    booking = await _booking(engine)

    owner = TelegramUser(groom_dp, user_id=ADMIN_ID)
    await owner.click(AdminCb(action="day", arg=booking.start_dt.astimezone(groom.booking.tz).date().isoformat()).pack())
    assert "Іван · 🐾 Лакі" in owner.last_text()
    await owner.click(AdminCb(action="bk", id=booking.id).pack())
    assert "➕ Додатково: Чищення зубів" in owner.last_text()
    assert "💵 Вартість: 1150 грн" in owner.last_text()

    async with create_session_factory(engine)() as session:
        ids = {s.name: s.id for s in (await session.scalars(select(Service))).all()}
    await owner.click(AdminCb(action="svc", id=ids["Комплексний догляд"]).pack())
    assert "• собака, шпіц, до 4 кг: 120 хв · 1200 грн" in owner.last_text()
    assert "• собака, інша порода / метис, до 4 кг: 120 хв · від 900 грн" in owner.last_text()
    await owner.click(AdminCb(action="svc", id=ids["Чищення зубів"]).pack())
    assert texts.ADMIN_SERVICE_ADDON in owner.last_text()
    assert "• собака (усі): 15 хв · 150 грн" in owner.last_text()
    await owner.click(AdminCb(action="svc", id=ids["Спа для лап"]).pack())
    assert "немає в прайсі профілю" in owner.last_text()


async def test_groom_texts(groom_dp):
    assert texts.BTN_ADMIN_SERVICES == "🐩 Послуги"


async def test_groom_reminders_config(groom):
    assert groom.reminders.before_24h and not groom.reminders.before_2h
