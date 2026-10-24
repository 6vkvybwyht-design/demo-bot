"""Спа: ресурси (кабінети, термальна зона), гості, ціна, аванс, автоскасування, групова подія."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from aiogram.methods import SendMessage
from sqlalchemy import delete, select

from bot import texts
from bot.config import Settings, load_business_config, profile_path
from bot.db.models import Booking, BookingResource, BookingStatus, Client, Master, Service, TimeOff
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb
from bot.main import create_dispatcher
from bot.services import booking as booking_service
from bot.services import notifications
from bot.services import slots
from bot.services import spa as spa_service
from bot.services.booking import EventFullError, SlotUnavailableError, SpaExtras
from bot.services.deposits import expire_unpaid_bookings
from bot.services.reminders import pending_bookings
from bot.services.spa import GuestsError, Occupied, Use, fits_resources, quote
from seed.demo_data import reset_demo_data
from tests.conftest import ADMIN_ID
from tests.fake_telegram import TelegramUser

KYIV_OFFSET = timedelta(hours=2)  # зимовий час (листопад 2026)
TUESDAY = date(2026, 11, 3)
MONDAY = date(2026, 11, 2)
THURSDAY = date(2026, 11, 5)
FRIDAY = date(2026, 11, 6)


@pytest.fixture
def spa_business():
    return load_business_config(profile_path("spa"))


@pytest.fixture
def spa(spa_business):
    return spa_business.spa


@pytest.fixture
def rules(spa_business):
    return spa_business.booking


def at(day: date, hh: int, mm: int = 0) -> datetime:
    """Київський час дня `day` → UTC."""
    return datetime.combine(day, time(hh, mm), tzinfo=UTC) - KYIV_OFFSET


def svc(name: str, duration: int = 60, price: int = 0, sid: int = 1) -> Service:
    return Service(id=sid, name=name, duration_min=duration, price=price)


def demo_svc(business, name: str, sid: int = 1) -> Service:
    """Послуга з тривалістю й ціною, як у демо-даних профілю."""
    item = next(s for s in business.demo.services if s.name == name)
    return Service(id=sid, name=name, duration_min=item.duration_min, price=item.price)


MASSAGE = [Use("cabinet", 1, 0, 60)]
COUPLE = [Use("cabinet", 2, 0, 60)]
THERMAL_2H = [Use("thermal", 1, 0, 120)]


def occupied(plan, start: datetime) -> list[Occupied]:
    return [
        Occupied(u.resource, u.units, start + timedelta(minutes=u.start_min), start + timedelta(minutes=u.end_min))
        for u in plan
    ]


# --- Ресурси ------------------------------------------------------------------


def test_two_massages_at_once_yes_third_no(spa, rules):
    start = at(TUESDAY, 12)
    taken = occupied(MASSAGE, start)
    assert fits_resources(MASSAGE, start, taken, spa, rules)  # другий масаж — так
    taken += occupied(MASSAGE, start)
    assert not fits_resources(MASSAGE, start, taken, spa, rules)  # третій — ні
    # Частковий перетин теж: третій масаж о 12:30 не вміщається, о 13:00 — так
    assert not fits_resources(MASSAGE, at(TUESDAY, 12, 30), taken, spa, rules)
    assert fits_resources(MASSAGE, at(TUESDAY, 13), taken, spa, rules)


def test_massage_for_two_blocks_both_cabinets(spa, rules):
    start = at(TUESDAY, 12)
    assert fits_resources(COUPLE, start, [], spa, rules)
    # Масаж для двох займає обидва кабінети — звичайний масаж не вміщається
    assert not fits_resources(MASSAGE, start, occupied(COUPLE, start), spa, rules)
    # І навпаки: якщо один кабінет зайнятий, масажу для двох немає місця
    assert not fits_resources(COUPLE, at(TUESDAY, 11, 30), occupied(MASSAGE, start), spa, rules)
    # Кабінети не мають часу на підготовку: одразу після — можна
    assert fits_resources(COUPLE, at(TUESDAY, 13), occupied(MASSAGE, start), spa, rules)


def test_thermal_zone_does_not_overlap_and_needs_30_min_cleanup(spa, rules):
    taken = occupied(THERMAL_2H, at(TUESDAY, 14))  # 14:00–16:00
    assert not fits_resources(THERMAL_2H, at(TUESDAY, 15), taken, spa, rules)
    # Після 16:00 — ще 30 хв підготовки
    assert not fits_resources(THERMAL_2H, at(TUESDAY, 16), taken, spa, rules)
    assert fits_resources(THERMAL_2H, at(TUESDAY, 16, 30), taken, spa, rules)
    # Перед 14:00 теж потрібна підготовка: до 13:30 закінчити — можна, до 14:00 — ні
    assert fits_resources(THERMAL_2H, at(TUESDAY, 11, 30), taken, spa, rules)
    assert not fits_resources(THERMAL_2H, at(TUESDAY, 12), taken, spa, rules)
    # Масажі термальну зону не займають
    assert fits_resources(MASSAGE, at(TUESDAY, 14), taken, spa, rules)


def test_moroccan_hammam_takes_zone_and_cabinet_for_last_hour(spa, rules):
    plan = spa_service.plan_uses(spa, "Марокканський хамам", 150)
    assert plan == [Use("thermal", 1, 0, 150), Use("cabinet", 1, 90, 150)]
    start = at(TUESDAY, 12)  # кабінет — 13:30–14:30
    two_massages = occupied(MASSAGE, at(TUESDAY, 12)) * 2  # обидва кабінети 12:00–13:00
    assert fits_resources(plan, start, two_massages, spa, rules)
    two_massages_late = occupied(MASSAGE, at(TUESDAY, 14)) * 2  # обидва кабінети 14:00–15:00
    assert not fits_resources(plan, start, two_massages_late, spa, rules)
    # Для двох — обидва кабінети на останні 60 хв
    plan2 = spa_service.plan_uses(spa, "Марокканський хамам для двох", 180)
    assert Use("cabinet", 2, 120, 180) in plan2
    assert not fits_resources(plan2, start, occupied(MASSAGE, at(TUESDAY, 14, 30)), spa, rules)


def test_open_evening_blocks_thermal_zone_on_fridays(spa, rules):
    blocks = spa_service.event_blocks(spa, FRIDAY, rules)
    assert blocks == [Occupied("thermal", 1, at(FRIDAY, 18), at(FRIDAY, 21))]
    assert spa_service.event_blocks(spa, TUESDAY, rules) == []
    # Програма має закінчитися за 30 хв до вечора
    assert fits_resources(THERMAL_2H, at(FRIDAY, 15, 30), blocks, spa, rules)
    assert not fits_resources(THERMAL_2H, at(FRIDAY, 16), blocks, spa, rules)
    assert fits_resources(MASSAGE, at(FRIDAY, 18), blocks, spa, rules)


def test_resources_only_within_opening_hours(spa, rules):
    # Пн 13:00–21:00, Вт–Нд 10:00–21:00
    assert not fits_resources(MASSAGE, at(MONDAY, 12), [], spa, rules)
    assert fits_resources(MASSAGE, at(MONDAY, 13), [], spa, rules)
    assert fits_resources(MASSAGE, at(TUESDAY, 20), [], spa, rules)
    # Хамам після масажу: 20:00 масаж + година хамаму після 21:00 — ні
    plan = spa_service.plan_uses(spa, "Релакс масаж", 60, ["Хамам і басейн після масажу (1 особа)"])
    assert plan == [Use("cabinet", 1, 0, 60), Use("thermal", 1, 60, 120)]
    assert not fits_resources(plan, at(TUESDAY, 19, 30), [], spa, rules)
    assert fits_resources(plan, at(TUESDAY, 19), [], spa, rules)


# --- Гості й ціна -------------------------------------------------------------


def test_guest_limits(spa):
    def check(name, counts):
        quote(spa, svc(name), counts=counts)

    with pytest.raises(GuestsError, match="щонайменше 3"):
        check("«Повне Перезавантаження» для компанії", {"adults": 2})
    with pytest.raises(GuestsError, match="щонайбільше 6"):
        check("«Повне Перезавантаження» для компанії", {"adults": 7})
    with pytest.raises(GuestsError, match="щонайменше 6"):
        check("Банний вечір", {"adults": 5})
    with pytest.raises(GuestsError, match="щонайбільше 9"):
        check("СПА Корпоратив", {"adults": 10})
    with pytest.raises(GuestsError, match="щонайбільше 4"):
        check("Слов'янське паріння для компанії", {"adults": 5})
    # Відпочинок: разом щонайбільше 6 осіб, діти теж рахуються
    with pytest.raises(GuestsError, match="Разом щонайбільше 6"):
        check("Хамам і басейн", {"adults": 4, "kids8": 2, "kids7": 1})
    check("Хамам і басейн", {"adults": 4, "kids8": 1, "kids7": 1})
    # Подія: не більше вільних місць
    with pytest.raises(GuestsError, match="щонайбільше 3"):
        quote(spa, svc("Відкритий банний вечір (для дівчат)"), counts={"adults": 4}, max_first=3)


@pytest.mark.parametrize(
    ("name", "counts", "price", "duration"),
    [
        ("Хамам і басейн", {"adults": 2, "kids8": 0, "kids7": 0}, 5000, 120),
        ("Хамам і басейн", {"adults": 1, "kids8": 0, "kids7": 0}, 5000, 120),
        # 2 дорослих у ціні + 2 × 1500, дитина 8–13 — 500, до 7 — безкоштовно
        ("Хамам і басейн", {"adults": 4, "kids8": 1, "kids7": 1}, 8500, 120),
        ("Хамам, сауна і басейн", {"adults": 3, "kids8": 0, "kids7": 0}, 7000, 120),
        ("Хамам, сауна і басейн", {"adults": 5, "kids8": 0, "kids7": 1}, 10000, 120),
        ("«Повне Перезавантаження» для компанії", {"adults": 3}, 11000, 180),
        ("«Повне Перезавантаження» для компанії", {"adults": 5}, 17000, 180),
        ("Слов'янське паріння для компанії", {"adults": 3}, 9500, 150),
        ("Слов'янське паріння для компанії", {"adults": 4}, 12000, 180),
        ("Турецький хамам для компанії", {"adults": 4}, 12000, 180),
        ("Банний вечір", {"adults": 7}, 17500, 180),
        ("СПА Корпоратив", {"adults": 9}, 31500, 240),
        ("Відкритий банний вечір (для дівчат)", {"adults": 2}, 6000, 180),
    ],
)
def test_price_with_guests(spa_business, name, counts, price, duration):
    q = quote(spa_business.spa, demo_svc(spa_business, name), counts=counts)
    assert (q.price, q.duration_min) == (price, duration)
    assert q.guests == sum(counts.values())
    assert q.deposit == price // 2


def test_price_with_addons(spa):
    program = svc("Банне СПА «Повне Перезавантаження»", 120, 6000)
    brooms = svc("Індивідуальна пропарка дубовими віниками", 15, 1000, sid=2)
    peeling = svc("Пілінг всього тіла", 15, 800, sid=3)
    q = quote(spa, program, addons=[brooms, peeling])
    # Додаткові послуги до програми не подовжують її
    assert (q.price, q.duration_min, q.deposit) == (7800, 120, 3900)
    assert q.plan == [Use("thermal", 1, 0, 120)]

    massage = svc("Релакс масаж", 60, 1600)
    paraffin = svc("Парафінотерапія до масажу", 15, 500, sid=4)
    q = quote(spa, massage, variant=1, addons=[paraffin])
    assert (q.price, q.duration_min, q.deposit) == (2700, 105, None)  # масаж — без авансу

    # Хамам після масажу: аванс на все й термальна зона на годину після масажу
    rest = svc("Хамам і басейн після масажу (2 особи)", 60, 3500, sid=5)
    q = quote(spa, massage, variant=0, addons=[rest])
    assert (q.price, q.duration_min, q.deposit) == (5100, 60, 2550)
    assert q.plan == [Use("cabinet", 1, 0, 60), Use("thermal", 1, 60, 120)]

    # Рест із додатковим часом і гостями
    rest_zone = svc("Хамам і басейн", 120, 5000)
    extra = svc("Додатковий час, 60 хв", 60, 2000, sid=6)
    q = quote(spa, rest_zone, counts={"adults": 3, "kids8": 0, "kids7": 0}, addons=[extra])
    assert (q.price, q.duration_min) == (8500, 180)
    assert q.plan == [Use("thermal", 1, 0, 180)]


def test_couple_massage_needs_deposit(spa):
    q = quote(spa, svc("Масаж для двох", 60, 3700), variant=1)
    assert (q.price, q.duration_min, q.deposit) == (4900, 90, 2450)
    assert q.plan == [Use("cabinet", 2, 0, 90)]


def test_addon_groups_are_exclusive(spa):
    services = [svc("Додатковий час, 30 хв", sid=1), svc("Додатковий час, 60 хв", sid=2),
                svc("Метод сухої голки", sid=3)]
    selected = spa_service.toggle_addon([], services[0], spa, services)
    selected = spa_service.toggle_addon(selected, services[2], spa, services)
    selected = spa_service.toggle_addon(selected, services[1], spa, services)
    assert selected == [2, 3]  # +60 хв замінив +30 хв


# Прайс із PDF: назва → [(тривалість, ціна)] (варіанти) або (тривалість, ціна)
PRICE_LIST = {
    "Банне СПА «Колискова Тиші та Тепла»": (150, 6500),
    "Банне СПА «Повне Перезавантаження»": (120, 6000),
    "Банне СПА «Енергія Вогню»": (120, 6000),
    "Детокс Баня (тільки для дівчат)": (120, 6000),
    "Слов'янське паріння в бані": (90, 4500),
    "Спа ритуал «Насолода з островів Індонезії»": (120, 6000),
    "Марокканський хамам": (150, 6600),
    "Берберський хамам": (90, 5000),
    "Турецький хамам": (90, 4500),
    "«Повне Перезавантаження» для двох": (180, 10000),
    "«Енергія Вогню» для двох": (180, 10000),
    "Слов'янське паріння для двох": (120, 7500),
    "Марокканський хамам для двох": (180, 13000),
    "Турецький хамам для двох": (120, 7500),
    "Релакс масаж": [(60, 1600), (90, 2200)],
    "Оздоровчий масаж всього тіла": [(60, 1600), (90, 2200), (120, 3000)],
    "Спортивний масаж": [(60, 1600), (90, 2200)],
    "Масаж спини по тригерних точках": [(60, 2000)],
    "Масаж гуаша": [(60, 1600), (90, 2200)],
    "Масаж блейдами (IASTM)": [(60, 1600), (90, 2200)],
    "Міофасціальний реліз (МФР)": [(60, 1600), (90, 2200)],
    "Стоун-масаж": [(60, 1800), (90, 2300)],
    "Лімфодренажний масаж": [(60, 1600), (90, 2200)],
    "Масаж спини": [(30, 1100), (45, 1300), (60, 1600)],
    "Вісцеральний масаж": [(60, 2000)],
    "Краніосакральна терапія": [(60, 1600), (90, 2200), (120, 3000)],
    "Масаж тіла та обличчя": [(120, 3000)],
    "Масаж для двох": [(60, 3700), (90, 4900)],
    "Масаж для вагітних": [(60, 1600), (90, 2200)],
    "Дитячий масаж": [(30, 1100), (45, 1300)],
    "Антицелюлітний масаж": [(45, 1400), (60, 1700), (90, 2300)],
    "Масаж обличчя": [(60, 1600)],
    "Тайський масаж стоп": [(60, 1600)],
    "Аромамасаж свічкою": [(60, 1800), (90, 2300)],
}
ADDON_PRICES = {
    "Індивідуальна пропарка дубовими віниками": 1000, "Мильний пінний масаж": 1000,
    "Пілінг всього тіла": 800, "Глазурування глиною гассул": 1000,
    "Глазурування білою глиною з дамаською трояндою": 1000, "Глазурування рожевою глиною": 800,
    "Парафінотерапія стоп або рук": 500, "Зволоження всього тіла": 500,
    "Масаж бамбуковими віниками": 600, "Масаж однієї ділянки, 15 хв": 600,
    "Контрастні процедури мінеральною водою": 400, "Ванночки для ніг": 100,
    "Консультація на Таро": 600, "Парафінотерапія до масажу": 500, "Метод сухої голки": 500,
    "Тейпування (1 ділянка)": 500, "Догляд Ed Cosmetics «Вогонь і Лід»": 1200,
    "Догляд Flow of Beauty": 600, "Хамам і басейн після масажу (1 особа)": 2500,
    "Хамам і басейн після масажу (2 особи)": 3500, "Додатковий час, 30 хв": 1000,
    "Додатковий час, 60 хв": 2000,
}


def test_prices_match_price_list(spa_business):
    spa = spa_business.spa
    demo = {s.name: s for s in spa_business.demo.services}
    for name, expected in PRICE_LIST.items():
        service = svc(name, demo[name].duration_min, demo[name].price)
        if isinstance(expected, list):
            got = [(v.duration_min, v.price) for v in spa.service(name).variants]
            assert got == expected, name
            for i, (duration, price) in enumerate(expected):
                assert (quote(spa, service, variant=i).duration_min, quote(spa, service, variant=i).price) == (duration, price)
        else:
            q = quote(spa, service)
            assert (q.duration_min, q.price) == expected, name
    for name, price in ADDON_PRICES.items():
        assert demo[name].price == price, name
        assert spa.service(name).addon
    # Ресурси: програми — термальна зона, масажі — кабінет, відпочинок — без майстра
    for name, cfg in spa.services.items():
        if cfg.category in {"bath", "hammam", "two", "company", "rest"}:
            assert any(u.resource == "thermal" for u in cfg.uses), name
            assert cfg.deposit, name
        if cfg.category == "massage":
            assert cfg.uses[0].resource == "cabinet", name
        if cfg.category == "rest":
            assert not cfg.staff


# --- База: слоти, бронювання, аванс, подія ------------------------------------


NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # Пн, 10:00 за Києвом


@pytest.fixture
async def spa_db(session, spa_business):
    """Демо-спа без записів: послуги, майстри, графік."""
    await reset_demo_data(session, spa_business, now=NOW)
    await session.execute(delete(BookingResource))
    await session.execute(delete(Booking))
    await session.commit()
    return session


async def _service(session, name) -> Service:
    return await session.scalar(select(Service).where(Service.name == name))


async def _master(session, name) -> Master:
    return await session.scalar(select(Master).where(Master.name.startswith(name)))


async def _client(session, tg=1000) -> Client:
    return await booking_service.get_or_create_client(session, tg, f"Клієнт {tg}")


async def _book(session, spa_business, name, master, start, *, variant=None, counts=None, addons=(), tg=1000):
    spa = spa_business.spa
    service = await _service(session, name)
    addon_services = [await _service(session, a) for a in addons]
    q = quote(spa, service, variant=variant, counts=counts, addons=addon_services)
    master_id = (await _master(session, master)).id if master else None
    return await booking_service.create_booking(
        session, client=await _client(session, tg), service=service, master_id=master_id,
        start=start, rules=spa_business.booking, now=NOW, reminders=spa_business.reminders,
        duration_min=q.duration_min, price=q.price,
        extras=SpaExtras(
            resources=spa_service.resource_filter(spa, q.plan), guests=q.guests,
            guests_details=q.guests_details, deposit=q.deposit, hold_min=spa.deposit.hold_min,
            staff_count=q.staff_count,
        ),
    )


async def test_db_two_massages_yes_third_no(spa_db, spa_business):
    start = at(TUESDAY, 12)
    await _book(spa_db, spa_business, "Релакс масаж", "Олена", start)
    await _book(spa_db, spa_business, "Релакс масаж", "Марко", start)
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Релакс масаж", "Тарас", start)
    # Тарас вільний, але слоту о 12:00 у нього немає — кабінети зайняті
    service = await _service(spa_db, "Релакс масаж")
    plan = spa_service.plan_uses(spa_business.spa, service.name, 60)
    free = await slots.get_free_slots(
        spa_db, master_id=(await _master(spa_db, "Тарас")).id, service=service, day=TUESDAY,
        rules=spa_business.booking, now=NOW, resources=spa_service.resource_filter(spa_business.spa, plan),
    )
    assert start not in free and at(TUESDAY, 11) in free and at(TUESDAY, 13) in free
    # «Будь-який вільний» теж не пропонує 12:00
    any_free = await slots.get_free_slots_any_master(
        spa_db, service=service, day=TUESDAY, rules=spa_business.booking, now=NOW,
        resources=spa_service.resource_filter(spa_business.spa, plan),
    )
    assert start not in any_free


async def test_db_massage_for_two_blocks_both_cabinets(spa_db, spa_business):
    start = at(TUESDAY, 15)
    await _book(spa_db, spa_business, "Масаж для двох", "Софія", start)
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Релакс масаж", "Марко", at(TUESDAY, 15, 30))
    await _book(spa_db, spa_business, "Релакс масаж", "Марко", at(TUESDAY, 16))


async def _time_off(session, master: str, start: datetime, end: datetime) -> None:
    session.add(TimeOff(master_id=(await _master(session, master)).id, start_dt=start, end_dt=end))
    await session.commit()


async def test_db_massage_for_two_needs_two_free_masters(spa_db, spa_business):
    service = await _service(spa_db, "Масаж для двох")
    assert quote(spa_business.spa, service).staff_count == 2
    start = at(TUESDAY, 12)
    # Вільна лише Олена: Марко, Софія й Тарас 12:00–13:00 недоступні (кабінети вільні)
    for name in ("Марко", "Софія", "Тарас"):
        await _time_off(spa_db, name, start, at(TUESDAY, 13))
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Масаж для двох", "Олена", start, variant=0)
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Масаж для двох", None, start, variant=0)
    service = await _service(spa_db, "Масаж для двох")  # після rollback об'єкти застаріли
    plan = spa_service.plan_uses(spa_business.spa, service.name, 60)
    common = dict(
        service=service, day=TUESDAY, rules=spa_business.booking, now=NOW, duration_min=60,
        resources=spa_service.resource_filter(spa_business.spa, plan), staff_count=2,
    )
    olena = (await _master(spa_db, "Олена")).id
    assert start not in await slots.get_free_slots(spa_db, master_id=olena, **common)
    assert start not in await slots.get_free_slots_any_master(spa_db, **common)
    # Без вимоги двох майстрів Олена о 12:00 вільна — заважає саме відсутність другого
    assert start in await slots.get_free_slots(
        spa_db, master_id=olena, **{**common, "staff_count": 1}
    )
    assert at(TUESDAY, 13) in await slots.get_free_slots(spa_db, master_id=olena, **common)

    # Звільняється Тарас — запис можливий, він стає другим майстром
    await spa_db.execute(delete(TimeOff).where(TimeOff.master_id == (await _master(spa_db, "Тарас")).id))
    await spa_db.commit()
    booking = await _book(spa_db, spa_business, "Масаж для двох", "Олена", start, variant=0)
    taras = (await _master(spa_db, "Тарас")).id
    assert (booking.master_id, booking.second_master_id) == (olena, taras)
    # Обидва майстри зайняті (перевіряємо без ресурсів: кабінети теж зайняті)
    relax = await _service(spa_db, "Релакс масаж")
    for master_id in (olena, taras):
        free = await slots.get_free_slots(
            spa_db, master_id=master_id, service=relax, day=TUESDAY, rules=spa_business.booking,
            now=NOW, duration_min=60,
        )
        assert start not in free and at(TUESDAY, 13) in free


async def test_db_massage_for_two_any_master_picks_two_different(spa_db, spa_business):
    booking = await _book(spa_db, spa_business, "Масаж для двох", None, at(TUESDAY, 15), variant=1)
    assert booking.second_master_id is not None and booking.second_master_id != booking.master_id
    # Обидва майстри є в картці запису й у сповіщеннях
    loaded = await notifications.load_booking(spa_db, booking.id)
    assert ", " in loaded.master_names
    loaded.master.telegram_id, loaded.second_master.telegram_id = 601, 602
    assert notifications.staff_recipients(loaded, [ADMIN_ID]) == [ADMIN_ID, 601, 602]


async def test_db_massage_for_two_reschedule_keeps_both_masters(spa_db, spa_business):
    start = at(THURSDAY, 12)
    booking = await _book(spa_db, spa_business, "Масаж для двох", "Олена", start, variant=0)
    await booking_service.pay_deposit(spa_db, booking_id=booking.id, now=NOW)
    master_id, partner_id = booking.master_id, booking.second_master_id
    partner = await spa_db.get(Master, partner_id)
    # Другий майстер зайнятий о 15:00 — перенести туди не можна, хоч інші вільні
    await _time_off(spa_db, partner.name, at(THURSDAY, 15), at(THURSDAY, 16))
    common = dict(booking_id=booking.id, telegram_id=1000, rules=spa_business.booking, now=NOW,
                  spa=spa_business.spa)
    with pytest.raises(SlotUnavailableError):
        await booking_service.reschedule_booking(spa_db, new_start=at(THURSDAY, 15), **common)
    moved = await booking_service.reschedule_booking(spa_db, new_start=at(THURSDAY, 17), **common)
    assert (moved.master_id, moved.second_master_id) == (master_id, partner_id)
    assert moved.start_dt == at(THURSDAY, 17)


async def test_db_thermal_zone_with_cleanup(spa_db, spa_business):
    await _book(spa_db, spa_business, "Турецький хамам", "Богдан", at(TUESDAY, 12))  # 12:00–13:30
    # Інший пар-майстер вільний, але зона — ні; відпочинок без майстра — теж
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Берберський хамам", "Ірина", at(TUESDAY, 13, 30))
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Хамам і басейн", None, at(TUESDAY, 13, 30))
    booking = await _book(spa_db, spa_business, "Хамам і басейн", None, at(TUESDAY, 14))
    assert booking.master_id == (await _master(spa_db, "Термальна зона")).id


async def test_db_reschedule_moves_resources(spa_db, spa_business):
    booking = await _book(spa_db, spa_business, "Марокканський хамам", "Ірина", at(THURSDAY, 12))
    await booking_service.pay_deposit(spa_db, booking_id=booking.id, now=NOW)
    await _book(spa_db, spa_business, "Релакс масаж", "Олена", at(THURSDAY, 17))
    await _book(spa_db, spa_business, "Релакс масаж", "Марко", at(THURSDAY, 17))
    common = dict(booking_id=booking.id, telegram_id=1000, rules=spa_business.booking, now=NOW,
                  spa=spa_business.spa)
    # 15:00 → кабінет 16:30–17:30, а о 17:00 обидва кабінети зайняті
    with pytest.raises(SlotUnavailableError):
        await booking_service.reschedule_booking(spa_db, new_start=at(THURSDAY, 15), **common)
    moved = await booking_service.reschedule_booking(spa_db, new_start=at(THURSDAY, 14), **common)
    rows = sorted((r.resource, r.start_dt, r.end_dt) for r in moved.resources)
    assert rows == [("cabinet", at(THURSDAY, 15, 30), at(THURSDAY, 16, 30)),
                    ("thermal", at(THURSDAY, 14), at(THURSDAY, 16, 30))]


async def test_db_deposit_hold_and_auto_cancel(spa_db, spa_business):
    booking = await _book(spa_db, spa_business, "Слов'янське паріння в бані", "Богдан", at(TUESDAY, 12))
    assert booking.deposit == 2250 and booking.deposit_pending
    assert booking.hold_until == NOW + timedelta(minutes=30)
    booking_id = booking.id
    # Нагадування не надсилаються, поки бронювання не підтверджене
    assert booking_id not in [b.id for b in await pending_bookings(spa_db, at(TUESDAY, 9))]
    # Поки аванс не внесено, час утримується: зону не можна забронювати
    with pytest.raises(SlotUnavailableError):
        await _book(spa_db, spa_business, "Турецький хамам", "Ірина", at(TUESDAY, 12), tg=2000)

    # Через 29 хв — ще тримаємо, через 30 — скасовуємо
    assert await booking_service.expire_unpaid(spa_db, NOW + timedelta(minutes=29)) == []
    expired = await booking_service.expire_unpaid(spa_db, NOW + timedelta(minutes=30))
    assert [b.id for b in expired] == [booking_id]
    assert expired[0].status is BookingStatus.EXPIRED
    # Час звільнився
    await _book(spa_db, spa_business, "Турецький хамам", "Ірина", at(TUESDAY, 12), tg=2000)


async def test_db_paid_deposit_is_not_cancelled(spa_db, spa_business):
    booking = await _book(spa_db, spa_business, "Масаж для двох", "Олена", at(TUESDAY, 12), variant=0)
    paid = await booking_service.pay_deposit(spa_db, booking_id=booking.id, now=NOW, telegram_id=1000)
    assert paid.deposit_paid_at == NOW and not paid.deposit_pending and paid.hold_until is None
    assert await booking_service.expire_unpaid(spa_db, NOW + timedelta(hours=1)) == []
    # Чужий клієнт оплатити не може
    other = await _book(spa_db, spa_business, "Масаж для двох", "Марко", at(TUESDAY, 14), tg=2000)
    with pytest.raises(booking_service.BookingNotFoundError):
        await booking_service.pay_deposit(spa_db, booking_id=other.id, now=NOW, telegram_id=1000)
    # Звичайний масаж — без авансу й одразу підтверджений
    massage = await _book(spa_db, spa_business, "Релакс масаж", "Тарас", at(TUESDAY, 18))
    assert massage.deposit is None and not massage.deposit_pending


async def test_db_deposit_change_deadline(spa_db, spa_business):
    booking = await _book(spa_db, spa_business, "Турецький хамам", "Ірина", at(TUESDAY, 12))
    rules = spa_business.booking
    day_before = at(TUESDAY, 12) - timedelta(hours=30)
    # Не оплачено — скасувати можна будь-коли, перенести — ні
    assert booking_service.can_client_cancel(booking, rules, at(TUESDAY, 11))
    await booking_service.pay_deposit(spa_db, booking_id=booking.id, now=NOW)
    # Оплачено — лише за 48 год
    assert booking_service.change_deadline_hours(booking, rules) == 48
    assert not booking_service.can_client_change(booking, rules, day_before)
    assert booking_service.can_client_change(booking, rules, at(TUESDAY, 12) - timedelta(hours=49))


async def test_db_event_seats(spa_db, spa_business):
    spa = spa_business.spa
    service_name = "Відкритий банний вечір (для дівчат)"
    service = await _service(spa_db, service_name)
    cfg = spa.service(service_name)
    start = at(FRIDAY, 18)

    async def book(guests, tg):
        # Після відмови (rollback) об'єкти сесії застарівають — беремо послугу заново
        service = await _service(spa_db, service_name)
        q = quote(spa, service, counts={"adults": guests})
        return await booking_service.create_event_booking(
            spa_db, client=await _client(spa_db, tg), service=service, start=start,
            capacity=cfg.event.capacity, duration_min=q.duration_min, price=q.price,
            rules=spa_business.booking, now=NOW,
            extras=SpaExtras(guests=q.guests, guests_details=q.guests_details, deposit=q.deposit),
        )

    first = await book(6, 1)
    assert (first.price, first.deposit, first.end_dt) == (18000, 9000, at(FRIDAY, 21))
    first_id = first.id
    dates = dict(await spa_service.event_dates(spa_db, service, cfg, spa_business.booking, NOW))
    assert dates[start] == 3
    with pytest.raises(EventFullError):
        await book(4, 2)
    await book(3, 3)
    service = await _service(spa_db, service_name)
    dates = dict(await spa_service.event_dates(spa_db, service, cfg, spa_business.booking, NOW))
    assert start not in dates  # місць немає — дату не показуємо
    # Скасоване бронювання звільняє місця
    (await spa_db.get(Booking, first_id)).status = BookingStatus.CANCELLED_BY_CLIENT
    await spa_db.commit()
    assert dict(await spa_service.event_dates(spa_db, service, cfg, spa_business.booking, NOW))[start] == 6


async def test_expire_job_notifies_client_and_owner(engine, spa_business):
    factory = create_session_factory(engine)
    async with factory() as session:
        await reset_demo_data(session, spa_business, now=NOW)
        await session.execute(delete(BookingResource))
        await session.execute(delete(Booking))
        await session.commit()
        booking = await _book(session, spa_business, "Турецький хамам", "Ірина", at(TUESDAY, 12), tg=777)
    user = TelegramUser(create_dispatcher(_settings(), spa_business, factory))
    count = await expire_unpaid_bookings(
        user.bot, factory, spa_business, admin_ids=[ADMIN_ID], now=NOW + timedelta(minutes=31)
    )
    assert count == 1
    assert "аванс не внесено вчасно" in user.sent_to(777)[0]
    assert "скасовано автоматично" in user.sent_to(ADMIN_ID)[0]
    async with factory() as session:
        assert (await session.get(Booking, booking.id)).status is BookingStatus.EXPIRED


def test_profile_validation(spa_business):
    data = spa_business.model_dump(by_alias=True)
    data["spa"]["services"]["Релакс масаж"]["uses"] = [{"resource": "sauna"}]
    with pytest.raises(ValueError, match="невідомі ресурси: sauna"):
        type(spa_business).model_validate(data)
    data = spa_business.model_dump(by_alias=True)
    data["spa"]["services"]["Релакс масаж"]["addons"] = ["Релакс масаж"]
    with pytest.raises(ValueError, match="не додаткова послуга"):
        type(spa_business).model_validate(data)


# --- Наскрізні сценарії в Telegram --------------------------------------------


def _settings(demo_mode: bool = True) -> Settings:
    return Settings(_env_file=None, bot_token="42:TEST", admin_ids=[ADMIN_ID], demo_mode=demo_mode)


@pytest.fixture
async def spa_dp(engine, spa_business):
    factory = create_session_factory(engine)
    async with factory() as session:
        await reset_demo_data(session, spa_business)
        await session.commit()
    return create_dispatcher(_settings(), spa_business, factory)


@pytest.fixture
def guest(spa_dp):
    return TelegramUser(spa_dp, user_id=777, name="Марія")


def _any_button(user: TelegramUser, text: str) -> str:
    """Кнопка з будь-якого повідомлення в чаті (не лише з останнього)."""
    for request in reversed(user._own()):
        markup = getattr(request, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", []):
            for button in row:
                if button.text == text:
                    return button.callback_data
    raise AssertionError(f"немає кнопки «{text}»")


def _button_starting(user: TelegramUser, prefix: str) -> str:
    for label, data in user.buttons():
        if label.startswith(prefix):
            return data
    raise AssertionError(f"немає кнопки «{prefix}…» серед {[t for t, _ in user.buttons()]}")


async def _pick_first_date_and_time(user: TelegramUser) -> None:
    dates = [d for _, d in user.buttons() if d.startswith("day:")]
    assert dates, user.last_text()
    await user.click(dates[-1])  # найдальша дата — найменше демо-записів
    await user.click(user.slot_buttons()[0][1])


async def test_start_menu_has_spa_buttons(guest):
    await guest.send("/start")
    assert "SHUM VODY" in guest.last_text()
    buttons = [b.text for row in guest.last_reply_keyboard().keyboard for b in row]
    assert texts.BTN_CERTIFICATE in buttons and texts.BTN_RULES in buttons
    await guest.send(texts.BTN_RULES)
    assert "Стрибати в басейн заборонено" in guest.last_text()


async def test_massage_flow_without_deposit(guest):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("💆 Масаж"))
    await guest.click(_button_starting(guest, "Релакс масаж"))
    assert "Оберіть тривалість" in guest.last_text()
    await guest.click(guest.button("90 хв · 2200 грн"))
    # Додаткові послуги до масажу
    assert "Додаткові послуги" in guest.last_text()
    await guest.click(_button_starting(guest, "▫️ Парафінотерапія до масажу"))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    assert texts.CHOOSE_MASTER in guest.last_text()
    assert "Будь-який вільний" in guest.buttons()[0][0]
    await guest.click(guest.buttons()[0][1])
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    text = guest.last_text()
    assert "105 хв" in text and "2700 грн" in text and "Парафінотерапія" in text
    assert "Аванс" not in text
    await guest.click(guest.button(texts.BTN_CONFIRM))
    assert any("Ви записані" in t for t in guest.all_texts())


async def test_program_flow_with_deposit_and_demo_payment(guest, engine):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("🛁 Програми в хамамі"))
    await guest.click(_button_starting(guest, "Турецький хамам"))
    assert "кесе" in guest.last_text()  # короткий опис
    await guest.click(guest.button(texts.BTN_SPA_MORE))
    assert "мильний пінний масаж, користування басейном" in guest.last_text()  # повний опис
    await guest.click(guest.button(texts.BTN_SPA_PICK))
    await guest.click(_button_starting(guest, "▫️ Ванночки для ніг"))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(guest.buttons()[0][1])  # будь-який вільний пар-майстер
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    confirm = guest.last_text()
    assert "4600 грн" in confirm and "Аванс 50%: <b>2300 грн</b>" in confirm
    assert "48 год" in confirm and "30 хв" in confirm  # правила перед оплатою
    await guest.click(guest.button(texts.BTN_CONFIRM))
    held = next(t for t in reversed(guest.all_texts()) if "Час заброньовано" in t)
    assert "Час заброньовано до" in held and "2300 грн" in held
    pay = _any_button(guest, texts.BTN_PAY_DEPOSIT.format(amount=2300))
    # Демо: відвідувач бачить сповіщення власника з кнопками авансу
    assert "очікує авансу" in guest.last_text()
    assert texts.BTN_DEPOSIT_RECEIVED in [t for t, _ in guest.buttons()]

    await guest.click(pay)
    assert any("Аванс отримано (ДЕМО)" in t for t in guest.all_texts())
    async with create_session_factory(engine)() as session:
        booking = await session.scalar(select(Booking).join(Client).where(Client.telegram_id == 777))
        assert booking.deposit == 2300 and booking.deposit_paid_at is not None


async def test_owner_marks_deposit_received(spa_dp, guest, engine):
    owner = TelegramUser(spa_dp, user_id=ADMIN_ID, name="Власник")
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("💑 Спа-ритуали для двох"))
    await guest.click(_button_starting(guest, "Слов'янське паріння для двох"))
    await guest.click(guest.button(texts.BTN_SPA_PICK))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(guest.buttons()[0][1])
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    assert "👥 Гості: 2" in guest.last_text()
    await guest.click(guest.button(texts.BTN_CONFIRM))
    notification = guest.sent_to(ADMIN_ID)[-1]
    assert "очікує авансу" in notification and "Аванс 3750 грн — очікуємо оплату" in notification

    async with create_session_factory(engine)() as session:
        booking = await session.scalar(select(Booking).join(Client).where(Client.telegram_id == 777))
    await owner.click(AdminCb(action="dep_ok", id=booking.id).pack())
    assert "внесено ✅" in owner.last_text()
    assert "Бронювання підтверджено" in owner.sent_to(777)[-1]


async def test_rest_flow_skips_master_and_counts_guests(guest):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("♨️ Відпочинок у термальній зоні"))
    await guest.click(_button_starting(guest, "Хамам і басейн"))
    await guest.click(guest.button(texts.BTN_SPA_PICK))
    assert "Скільки гостей" in guest.last_text() and "5000 грн" in guest.last_text()
    plus_adults = [d for t, d in guest.buttons() if d == "guest:adults:1"][0]
    plus_kids = [d for t, d in guest.buttons() if d == "guest:kids8:1"][0]
    await guest.click(plus_adults)  # 3 дорослих
    await guest.click(plus_kids)  # + дитина 8–13
    assert "7000 грн" in guest.last_text()
    for _ in range(3):
        await guest.click(plus_adults)  # 6 дорослих + дитина = 7 > 6
    assert "Разом щонайбільше 6" in guest.session.requests[-1].text  # alert, кількість не змінилась
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(_button_starting(guest, "▫️ Додатковий час, 30 хв"))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    # Без вибору майстра — одразу дата
    assert texts.CHOOSE_DATE in guest.last_text() and "будь-який" not in guest.last_text()
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    text = guest.last_text()
    assert "Майстер" not in text and "Дорослі: 5, Діти 8–13 років: 1" in text
    # 5000 + 3 дорослих понад двох × 1500 + дитина 500 + 30 хв 1000
    assert "150 хв" in text and "11000 грн" in text


async def test_open_evening_flow(guest):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("🌙 Відкритий банний вечір"))
    await guest.click(_button_starting(guest, "Відкритий банний вечір"))
    assert "до 9 місць" in guest.last_text()
    await guest.click(guest.button(texts.BTN_SPA_PICK))
    dates = [(t, d) for t, d in guest.buttons() if d.startswith("evt:")]
    assert dates and all("18:00" in t for t, _ in dates)
    assert "вільно 4" in dates[0][0]  # у демо на найближчий вечір 5 з 9 місць зайнято
    await guest.click(dates[0][1])
    assert "Вільних місць: 4" in guest.last_text()
    await guest.click("guest:adults:1")
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))  # гості → додаткові послуги
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))  # без додаткових
    await guest.send("0501234567")
    text = guest.last_text()
    assert "Учасниці: 2" in text and "6000 грн" in text and "18:00" in text
    await guest.click(guest.button(texts.BTN_CONFIRM))
    assert any("Час заброньовано" in t for t in guest.all_texts())


async def test_certificate_request(guest):
    await guest.send(texts.BTN_CERTIFICATE)
    await guest.click(guest.button(texts.BTN_CERT_AMOUNT))
    await guest.click(guest.button("3000 грн"))
    await guest.click(guest.button(texts.BTN_CERT_SEND))
    await guest.send("0671234567")
    notification = guest.sent_to(ADMIN_ID)[-1]
    assert "сертифікат" in notification and "Сума: 3000 грн" in notification and "+380671234567" in notification

    await guest.send(texts.BTN_CERTIFICATE)
    await guest.click(guest.button(texts.BTN_CERT_SERVICE))
    await guest.click(guest.button("💆 Масаж"))
    await guest.click(_button_starting(guest, "Стоун-масаж"))
    await guest.click(guest.button(texts.BTN_CERT_SEND))
    assert "Послуга: Стоун-масаж" in guest.sent_to(ADMIN_ID)[-1]


async def test_other_profiles_ignore_spa_handlers(dp, user):
    # Barber: «Записатися» веде до послуг, а не до розділів спа; кнопок спа немає
    await user.send("/start")
    buttons = [b.text for row in user.last_reply_keyboard().keyboard for b in row]
    assert texts.BTN_CERTIFICATE not in buttons and texts.BTN_RULES not in buttons
    await user.send(texts.BTN_BOOK)
    assert texts.CHOOSE_SERVICE in user.last_text()
    requests = await user.send(texts.BTN_RULES)
    assert not [r for r in requests if isinstance(r, SendMessage)]


async def test_massage_for_two_flow_assigns_two_masters(guest):
    await guest.send(texts.BTN_BOOK)
    await guest.click(guest.button("💆 Масаж"))
    await guest.click(_button_starting(guest, "Масаж для двох"))
    assert "двоє майстрів" in guest.last_text()
    await guest.click(guest.button("60 хв · 3700 грн"))
    await guest.click(guest.button(texts.BTN_ADDONS_NEXT))
    await guest.click(guest.button("Олена"))
    await _pick_first_date_and_time(guest)
    await guest.send("0501234567")
    assert "Олена" + texts.SECOND_MASTER_AUTO in guest.last_text()
    await guest.click(guest.button(texts.BTN_CONFIRM))
    held = next(t for t in reversed(guest.all_texts()) if "Час заброньовано" in t)
    assert "👤 Майстер: Олена, " in held  # другого майстра призначено
