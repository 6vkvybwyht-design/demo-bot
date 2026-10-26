"""Профіль spa_demo: нейтральне демо для будь-яких спа-салонів на механіці профілю spa."""

from datetime import date

import pytest
from sqlalchemy import select

from bot import texts
from bot.config import load_business_config, profile_path
from bot.db.models import Booking, Client
from bot.db.session import create_session_factory
from bot.main import create_dispatcher
from bot.services import spa as spa_service
from bot.services.spa import Occupied, Use, fits_resources, quote
from seed.demo_data import reset_demo_data
from tests.conftest import ADMIN_ID
from tests.fake_telegram import TelegramUser
from tests.test_spa import (
    _any_button,
    _button_starting,
    _pick_first_date_and_time,
    _settings,
    at,
    demo_svc,
)

FRIDAY = date(2026, 11, 6)
TUESDAY = date(2026, 11, 3)

# Назва й авторські програми комплексу, з якого зроблено профіль spa
BRANDED = [
    "SHUM", "Колискова", "Індонезії", "Енергія Вогню", "Перезавантаження", "Детокс",
    "Берберський", "Марокканський", "Корпоратив", "Cello", "STYX", "Ed Cosmetics",
    "Flow of Beauty", "Таро",
]
REQUIRED = [
    "Релакс-масаж", "Оздоровчий масаж", "Масаж спини", "Спортивний масаж",
    "Антицелюлітний масаж", "Масаж обличчя", "Масаж для двох", "Турецький хамам",
    "Слов'янське паріння", "Хамам і басейн", "Сауна і басейн", "Хамам, сауна і басейн",
    "Турецький хамам для компанії", "Слов'янське паріння для компанії",
    "Банний вечір для компанії", "Відкритий банний вечір",
]


@pytest.fixture
def demo_spa():
    return load_business_config(profile_path("spa_demo"))


@pytest.fixture
def original():
    return load_business_config(profile_path("spa"))


def test_name_and_no_branded_content(demo_spa):
    assert demo_spa.business.name == "Demo Spa — онлайн-запис"
    text = profile_path("spa_demo").read_text(encoding="utf-8").lower()
    for word in BRANDED:
        assert word.lower() not in text, word


def test_general_services_present(demo_spa):
    names = {s.name for s in demo_spa.demo.services}
    assert set(REQUIRED) <= names
    for name in REQUIRED:
        assert demo_spa.spa.service(name).category is not None, name


def test_prices_are_rounded_and_differ_from_original(demo_spa, original):
    """Ціни округлені до 100 грн (дрібні додатки — до 50) і не скопійовані з прайсу spa."""
    spa, orig = demo_spa.spa, original.spa
    prices = []
    for item in demo_spa.demo.services:
        cfg = spa.service(item.name)
        variants = [(v.duration_min, v.price) for v in cfg.variants] or [(item.duration_min, item.price)]
        prices += [price for _, price in variants]
        same_name = next((s for s in original.demo.services if s.name == item.name), None)
        if same_name is not None and not cfg.addon:
            orig_variants = [(v.duration_min, v.price) for v in orig.service(item.name).variants] or [
                (same_name.duration_min, same_name.price)
            ]
            assert variants != orig_variants, item.name
    assert all(price % 50 == 0 for price in prices)
    assert all(price % 100 == 0 for price in prices if price >= 1000)


def test_mechanics_same_as_spa(demo_spa, original):
    spa, orig = demo_spa.spa, original.spa
    assert spa.resources == orig.resources  # 2 кабінети, термальна зона з 30 хв підготовки
    assert spa.opening_hours == orig.opening_hours
    assert (spa.deposit.percent, spa.deposit.hold_min) == (50, 30)
    assert demo_spa.booking.deposit_deadline_hours == 48
    assert spa.certificate_amounts
    couple = spa.service("Масаж для двох")
    assert couple.staff_count == 2 and couple.deposit and couple.uses[0].units == 2
    for name in ("Хамам і басейн", "Сауна і басейн", "Хамам, сауна і басейн"):
        assert not spa.service(name).staff and spa.service(name).uses[0].resource == "thermal"
    for name, cfg in spa.services.items():
        if cfg.category in {"programs", "two", "company", "rest", "event"}:
            assert cfg.deposit, name
        if cfg.category == "massage":
            assert cfg.uses[0].resource == "cabinet", name
    event = spa.service("Відкритий банний вечір").event
    assert event.blocks == ["thermal"] and event.weekdays == [4]


def test_resources_work_the_same(demo_spa):
    spa, rules = demo_spa.spa, demo_spa.booking
    massage = [Use("cabinet", 1, 0, 60)]
    start = at(TUESDAY, 12)
    two = [Occupied("cabinet", 1, start, at(TUESDAY, 13))] * 2
    assert not fits_resources(massage, start, two, spa, rules)  # третій масаж — ні
    thermal = [Occupied("thermal", 1, at(TUESDAY, 12), at(TUESDAY, 14))]
    program = spa_service.plan_uses(spa, "Турецький хамам", 90)
    assert not fits_resources(program, at(TUESDAY, 14), thermal, spa, rules)  # підготовка 30 хв
    assert fits_resources(program, at(TUESDAY, 14, 30), thermal, spa, rules)
    blocks = spa_service.event_blocks(spa, FRIDAY, rules)
    assert not fits_resources(program, at(FRIDAY, 16, 30), blocks, spa, rules)


@pytest.mark.parametrize(
    ("name", "counts", "price", "duration"),
    [
        ("Хамам і басейн", {"adults": 2, "kids8": 0, "kids7": 0}, 4500, 120),
        ("Хамам і басейн", {"adults": 4, "kids8": 1, "kids7": 1}, 7300, 120),
        ("Хамам, сауна і басейн", {"adults": 4, "kids8": 0, "kids7": 0}, 7700, 120),
        ("Турецький хамам для компанії", {"adults": 3}, 9000, 150),
        ("Турецький хамам для компанії", {"adults": 6}, 16500, 150),
        ("Слов'янське паріння для компанії", {"adults": 4}, 11500, 180),
        ("Банний вечір для компанії", {"adults": 8}, 20000, 180),
        ("Відкритий банний вечір", {"adults": 3}, 7500, 180),
    ],
)
def test_guest_prices(demo_spa, name, counts, price, duration):
    q = quote(demo_spa.spa, demo_svc(demo_spa, name), counts=counts)
    assert (q.price, q.duration_min, q.deposit) == (price, duration, price // 2)


def test_guest_limits(demo_spa):
    spa = demo_spa.spa
    with pytest.raises(spa_service.GuestsError, match="щонайбільше 6"):
        quote(spa, demo_svc(demo_spa, "Турецький хамам для компанії"), counts={"adults": 7})
    with pytest.raises(spa_service.GuestsError, match="Разом щонайбільше 6"):
        quote(spa, demo_svc(demo_spa, "Хамам і басейн"), counts={"adults": 5, "kids8": 2, "kids7": 0})


def test_original_spa_profile_unchanged(original):
    assert original.business.name == "SHUM VODY — онлайн-запис (демо)"
    assert original.spa.service("Банне СПА «Колискова Тиші та Тепла»") is not None


# --- Наскрізні сценарії в Telegram --------------------------------------------


@pytest.fixture
async def guest(engine, demo_spa):
    factory = create_session_factory(engine)
    async with factory() as session:
        await reset_demo_data(session, demo_spa)
        await session.commit()
    return TelegramUser(create_dispatcher(_settings(), demo_spa, factory), user_id=777, name="Марія")


async def test_start_and_rules(guest):
    await guest.send("/start")
    assert "Demo Spa — онлайн-запис" in guest.last_text()
    buttons = [b.text for row in guest.last_reply_keyboard().keyboard for b in row]
    assert texts.BTN_CERTIFICATE in buttons and texts.BTN_RULES in buttons
    await guest.send(texts.BTN_RULES)
    assert "Стрибати в басейн заборонено" in guest.last_text()


async def test_program_with_deposit(guest, engine):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("🔥 Спа-програми"))
    await guest.click(_button_starting(guest, "Турецький хамам"))
    await guest.click(guest.button(texts.BTN_SPA_PICK))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(guest.buttons()[0][1])
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    assert "4000 грн" in guest.last_text() and "Аванс 50%: <b>2000 грн</b>" in guest.last_text()
    await guest.click(guest.button(texts.BTN_CONFIRM))
    await guest.click(_any_button(guest, texts.BTN_PAY_DEPOSIT.format(amount=2000)))
    assert any("Аванс отримано (ДЕМО)" in t for t in guest.all_texts())
    assert "спа-салону" in guest.last_text()  # сповіщення адміністратора в демо
    async with create_session_factory(engine)() as session:
        booking = await session.scalar(select(Booking).join(Client).where(Client.telegram_id == 777))
        assert booking.deposit_paid_at is not None


async def test_massage_for_two_takes_two_masters(guest):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("💆 Масаж"))
    await guest.click(_button_starting(guest, "Масаж для двох"))
    await guest.click(guest.button("60 хв · 3400 грн"))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(guest.button("Анна"))
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    await guest.click(guest.button(texts.BTN_CONFIRM))
    held = next(t for t in reversed(guest.all_texts()) if "Час заброньовано" in t)
    assert "👤 Майстер: Анна, " in held
    assert guest.sent_to(ADMIN_ID)


async def test_certificate(guest):
    await guest.send(texts.BTN_CERTIFICATE)
    await guest.click(guest.button(texts.BTN_CERT_AMOUNT))
    await guest.click(guest.button("2000 грн"))
    await guest.click(guest.button(texts.BTN_CERT_SEND))
    await guest.send("0671234567")
    assert "Сума: 2000 грн" in guest.sent_to(ADMIN_ID)[-1]
