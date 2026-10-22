"""Хендлери клієнтського сценарію: /start і запис на послугу."""

from datetime import UTC, date, datetime

from aiogram import Bot, F, Router, html
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import BusinessConfig, Settings
from bot.db import repo
from bot.db.models import Master, Pet, Service, utcnow
from bot.keyboards import booking as kb
from bot.keyboards.callbacks import (
    ANY_MASTER,
    NEW_PET,
    DateCb,
    MasterCb,
    NavCb,
    NoopCb,
    PetCb,
    PetOptionCb,
    ServiceCb,
    TimeCb,
)
from bot.keyboards.main_menu import main_menu_kb
from bot.services import booking as booking_service
from bot.services import notifications, slots
from bot.services import pets as pets_service
from bot.services.booking import SlotUnavailableError
from bot.services.formatting import (
    booking_done_text,
    booking_summary,
    contacts_text,
    progress_text,
    summary_of,
)
from bot.services.phone import normalize_contact_phone, parse_ua_phone
from bot.states import BookingStates, RescheduleStates

router = Router(name="client")

Event = Message | CallbackQuery


@router.message(CommandStart())
async def cmd_start(
    message: Message, business: BusinessConfig, state: FSMContext, settings: Settings
) -> None:
    await state.clear()
    await message.answer(
        texts.WELCOME.format(name=html.quote(business.business.name)),
        reply_markup=main_menu_kb(settings.demo_mode),
    )


# --- Показ кроків -------------------------------------------------------------


async def _render(event: Event, text: str, reply_markup=None) -> None:
    """Для кнопки редагуємо поточне повідомлення, для тексту — надсилаємо нове."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


@router.message(F.text == texts.BTN_CONTACTS)
async def show_contacts(message: Message, business: BusinessConfig) -> None:
    await message.answer(
        contacts_text(business.business), reply_markup=kb.contacts_kb(business.business)
    )


async def _moving_booking_id(state: FSMContext) -> int | None:
    """id запису, якщо зараз триває перенесення, інакше None (звичайний запис)."""
    return (await state.get_data()).get("booking_id")


async def _load_choice(session: AsyncSession, state: FSMContext) -> tuple[Service, int | None]:
    data = await state.get_data()
    service = await session.get(Service, data["service_id"])
    return service, data.get("master_id")


# Порядок кроків для наростаючого підсумку
_PROGRESS_STEPS = ("service", "master", "day", "start", "phone")


async def _progress(
    prompt: str,
    state: FSMContext,
    session: AsyncSession,
    business: BusinessConfig,
    *,
    upto: str,
) -> str:
    """Підказка кроку з підсумком уже зроблених виборів (усіх кроків до `upto`)."""
    done = _PROGRESS_STEPS[: _PROGRESS_STEPS.index(upto)]
    data = await state.get_data()
    master_id = data.get("master_id")
    master = await session.get(Master, master_id) if "master" in done and master_id else None
    return progress_text(
        prompt,
        pet_name=data.get("pet_name"),
        service=await session.get(Service, data["service_id"]) if "service" in done else None,
        master_name=master.name if master else None,
        any_master="master" in done and master_id is None,
        day=date.fromisoformat(data["day"]) if "day" in done else None,
        start=datetime.fromisoformat(data["start"]) if "start" in done else None,
        tz=business.booking.tz,
    )


async def show_services(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    services = await repo.get_active_services(session)
    if not services:
        await state.clear()
        await _render(event, texts.NO_SERVICES.format(phone=business.business.phone))
        return
    data = await state.get_data()
    offers = None
    if business.pets is not None and "pet_size" in data:
        # Тривалість і ціна — для розміру тварини; недоступні для нього послуги не показуємо
        offers = {s.id: pets_service.offer_for(s, data["pet_size"], business.pets) for s in services}
        services = [s for s in services if offers[s.id] is not None]
        if not services:
            await state.set_state(BookingStates.service)
            await _render(
                event, texts.NO_SERVICES_FOR_PET.format(phone=business.business.phone), kb.back_kb()
            )
            return
    await state.set_state(BookingStates.service)
    text = await _progress(texts.CHOOSE_SERVICE, state, session, business, upto="service")
    await _render(event, text, kb.services_kb(services, offers))


async def show_masters(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, _ = await _load_choice(session, state)
    masters = await slots.masters_for_service(session, service.id)
    await state.set_state(BookingStates.master)
    if not masters:
        await _render(event, texts.NO_MASTERS, kb.back_kb())
        return
    text = await _progress(texts.CHOOSE_MASTER, state, session, business, upto="master")
    await _render(event, text, kb.masters_kb(masters))


async def show_dates(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    moving_id = await _moving_booking_id(state)
    rules = business.booking
    dates = await slots.get_available_dates(
        session, service=service, master_id=master_id, rules=rules, now=utcnow(),
        exclude_booking_id=moving_id, duration_min=(await state.get_data()).get("duration_min"),
    )
    await state.set_state(RescheduleStates.date if moving_id else BookingStates.date)
    if not dates:
        text = texts.NO_DATES.format(days=rules.horizon_days, phone=business.business.phone)
        await _render(event, text, kb.back_kb())
        return
    prompt = texts.MOVE_CHOOSE_DATE if moving_id else texts.CHOOSE_DATE
    text = await _progress(prompt, state, session, business, upto="day")
    await _render(event, text, kb.dates_kb(dates))


async def _free_starts(
    session: AsyncSession,
    service: Service,
    master_id: int | None,
    day: date,
    business: BusinessConfig,
    exclude_booking_id: int | None,
    duration_min: int | None,
) -> list[datetime]:
    common = dict(
        service=service, day=day, rules=business.booking, now=utcnow(), duration_min=duration_min
    )
    if master_id is None:
        return list(await slots.get_free_slots_any_master(session, **common))
    return await slots.get_free_slots(
        session, master_id=master_id, exclude_booking_id=exclude_booking_id, **common
    )


async def show_times(
    event: Event,
    state: FSMContext,
    session: AsyncSession,
    business: BusinessConfig,
    notice: str | None = None,
) -> None:
    service, master_id = await _load_choice(session, state)
    moving_id = await _moving_booking_id(state)
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    starts = await _free_starts(
        session, service, master_id, day, business, moving_id, data.get("duration_min")
    )
    if not starts:
        # Поки клієнт обирав, вільний час на цю дату закінчився
        await show_dates(event, state, session, business)
        return
    await state.set_state(RescheduleStates.time if moving_id else BookingStates.time)
    prompt = texts.MOVE_CHOOSE_TIME if moving_id else texts.CHOOSE_TIME
    text = await _progress(prompt, state, session, business, upto="start")
    if notice:
        text = f"{notice}\n\n{text}"
    await _render(event, text, kb.times_kb(starts, business.booking.tz))


async def show_confirm(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    service, master_id = await _load_choice(session, state)
    data = await state.get_data()
    start = datetime.fromisoformat(data["start"])
    master = await session.get(Master, master_id) if master_id else None
    pet = await session.get(Pet, data["pet_id"]) if data.get("pet_id") else None
    summary = booking_summary(
        service=service,
        master_name=master.name if master else None,
        start=start,
        tz=business.booking.tz,
        duration_min=data.get("duration_min"),
        price=data.get("price"),
        pet=pet,
        pets=business.pets,
    )
    await state.set_state(BookingStates.confirm)
    await _render(event, texts.CONFIRM.format(summary=summary), kb.confirm_kb())


# --- Кроки запису -------------------------------------------------------------


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.clear()
    if business.pets is not None:
        await show_pets(message, state, session, business)
    else:
        await show_services(message, state, session, business)


@router.callback_query(BookingStates.service, ServiceCb.filter())
async def pick_service(
    callback: CallbackQuery, callback_data: ServiceCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    service = await session.get(Service, callback_data.service_id)
    if service is None or not service.is_active:
        await show_services(callback, state, session, business)
        return
    if business.pets is not None:
        offer = pets_service.offer_for(service, (await state.get_data()).get("pet_size"), business.pets)
        if offer is None:  # послуга недоступна для цього розміру (стара кнопка)
            await show_services(callback, state, session, business)
            return
        await state.update_data(duration_min=offer.duration_min, price=offer.price)
    await state.update_data(service_id=service.id)
    await show_masters(callback, state, session, business)


@router.callback_query(BookingStates.master, MasterCb.filter())
async def pick_master(
    callback: CallbackQuery, callback_data: MasterCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    master_id = None if callback_data.master_id == ANY_MASTER else callback_data.master_id
    await state.update_data(master_id=master_id)
    await show_dates(callback, state, session, business)


@router.callback_query(StateFilter(BookingStates.date, RescheduleStates.date), DateCb.filter())
async def pick_date(
    callback: CallbackQuery, callback_data: DateCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    await state.update_data(day=callback_data.day)
    await show_times(callback, state, session, business, notice=None)


@router.callback_query(BookingStates.time, TimeCb.filter())
async def pick_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    start = datetime.fromtimestamp(callback_data.ts, tz=UTC)
    await state.update_data(start=start.isoformat())

    client = await booking_service.get_client(session, callback.from_user.id)
    if client is not None and client.phone:
        await show_confirm(callback, state, session, business)
        return

    await state.set_state(BookingStates.phone)
    await callback.message.edit_text(
        await _progress(texts.PHONE_STEP, state, session, business, upto="phone")
    )
    await callback.message.answer(texts.ASK_PHONE, reply_markup=kb.share_phone_kb())


@router.message(BookingStates.phone, F.contact)
async def got_phone(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(texts.PHONE_NOT_OWN, reply_markup=kb.share_phone_kb())
        return
    phone = normalize_contact_phone(message.contact.phone_number)
    await _phone_received(message, phone, state, session, business, settings)


async def _phone_received(
    message: Message, phone: str, state: FSMContext, session: AsyncSession,
    business: BusinessConfig, settings: Settings,
) -> None:
    await booking_service.save_client_phone(
        session, message.from_user.id, message.from_user.full_name, phone
    )
    await message.answer(texts.PHONE_SAVED, reply_markup=main_menu_kb(settings.demo_mode))
    await show_confirm(message, state, session, business)


@router.message(BookingStates.phone, F.text == texts.BTN_BACK)
async def phone_back(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    await message.answer(texts.BACK_TO_TIMES, reply_markup=main_menu_kb(settings.demo_mode))
    await show_times(message, state, session, business)


@router.message(BookingStates.phone, F.text)
async def got_phone_text(
    message: Message, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    settings: Settings,
) -> None:
    phone = parse_ua_phone(message.text)
    if phone is None:
        await message.answer(texts.PHONE_INVALID, reply_markup=kb.share_phone_kb())
        return
    await _phone_received(message, phone, state, session, business, settings)


@router.message(BookingStates.phone)
async def phone_expected(message: Message) -> None:
    # Стікер, фото тощо замість номера
    await message.answer(texts.PHONE_INVALID, reply_markup=kb.share_phone_kb())


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "confirm"))
async def confirm_booking(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig,
    bot: Bot, settings: Settings,
) -> None:
    service, master_id = await _load_choice(session, state)
    start = datetime.fromisoformat((await state.get_data())["start"])
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    data = await state.get_data()
    try:
        booking = await booking_service.create_booking(
            session, client=client, service=service, master_id=master_id,
            start=start, rules=business.booking, now=utcnow(), reminders=business.reminders,
            duration_min=data.get("duration_min"), price=data.get("price"),
            pet_id=data.get("pet_id"),
        )
    except SlotUnavailableError:
        await show_times(callback, state, session, business, notice=texts.SLOT_TAKEN)
        return

    await state.clear()
    booking = await notifications.load_booking(session, booking.id)
    summary = summary_of(booking, business)
    reminders = booking_service.planned_reminders(
        booking.start_dt, booking.created_at, business.reminders
    )
    await callback.message.edit_text(
        booking_done_text(summary=summary, address=business.business.address, reminders=reminders)
    )
    owner_text = await notifications.notify_staff(
        bot, session, booking_id=booking.id, template=texts.NOTIFY_NEW,
        admin_ids=settings.admin_ids, business=business,
        demo_mode=settings.demo_mode, actor_id=callback.from_user.id,
    )
    if settings.demo_mode:
        # Відвідувач демо одразу бачить і другу сторону — сповіщення власника
        await callback.message.answer(texts.DEMO_OWNER_VIEW.format(notification=owner_text))


@router.callback_query(NoopCb.filter())
async def noop(callback: CallbackQuery) -> None:
    # Заголовок групи слотів: нічого не робимо, «годинник» прибере CallbackAnswerMiddleware
    pass


# --- Тварина (профілі з розділом pets) ----------------------------------------


def _not_menu_button(text: str) -> bool:
    """Текст кнопки головного меню — не кличка й не порода (меню видно під час введення)."""
    return text not in {texts.BTN_BOOK, texts.BTN_MY_BOOKINGS, texts.BTN_CONTACTS, texts.BTN_DEMO_OWNER}


async def show_pets(
    event: Event, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    """Збережені тварини клієнта або одразу введення нової."""
    pets = await pets_service.client_pets(session, event.from_user.id)
    await state.set_data({})
    if not pets:
        await ask_pet_name(event, state)
        return
    await state.set_state(BookingStates.pet)
    await _render(event, texts.CHOOSE_PET, kb.pets_kb(pets, business.pets))


async def ask_pet_name(event: Event, state: FSMContext) -> None:
    await state.set_state(BookingStates.pet_name)
    await _render(event, texts.ASK_PET_NAME, kb.back_kb())


async def _ask_species(event: Event, state: FSMContext, business: BusinessConfig) -> None:
    name = (await state.get_data())["new_pet_name"]
    await state.set_state(BookingStates.pet_species)
    await _render(
        event, texts.ASK_PET_SPECIES.format(name=html.quote(name)), kb.pet_species_kb(business.pets)
    )


async def _ask_breed(event: Event, state: FSMContext) -> None:
    await state.set_state(BookingStates.pet_breed)
    await _render(event, texts.ASK_PET_BREED, kb.back_kb())


async def _ask_size(event: Event, state: FSMContext, business: BusinessConfig) -> None:
    await state.set_state(BookingStates.pet_size)
    await _render(event, texts.ASK_PET_SIZE, kb.pet_size_kb(business.pets))


async def _use_pet(
    event: Event, pet: Pet, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await state.set_data({"pet_id": pet.id, "pet_size": pet.size, "pet_name": pet.name})
    await show_services(event, state, session, business)


@router.callback_query(BookingStates.pet, PetCb.filter())
async def pick_pet(
    callback: CallbackQuery, callback_data: PetCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    if callback_data.pet_id == NEW_PET:
        await ask_pet_name(callback, state)
        return
    pet = await pets_service.get_own_pet(session, callback_data.pet_id, callback.from_user.id)
    if pet is None:
        await show_pets(callback, state, session, business)
        return
    await _use_pet(callback, pet, state, session, business)


@router.message(BookingStates.pet_name, F.text.func(_not_menu_button))
async def got_pet_name(message: Message, state: FSMContext, business: BusinessConfig) -> None:
    try:
        name = pets_service.parse_pet_name(message.text)
    except pets_service.PetInputError:
        await message.answer(texts.INVALID_PET_NAME, reply_markup=kb.back_kb())
        return
    await state.update_data(new_pet_name=name)
    await _ask_species(message, state, business)


@router.callback_query(BookingStates.pet_species, PetOptionCb.filter(F.kind == "species"))
async def got_pet_species(
    callback: CallbackQuery, callback_data: PetOptionCb, state: FSMContext, business: BusinessConfig
) -> None:
    if callback_data.key not in {s.key for s in business.pets.species}:
        await _ask_species(callback, state, business)
        return
    await state.update_data(new_pet_species=callback_data.key)
    await _ask_breed(callback, state)


@router.message(BookingStates.pet_breed, F.text.func(_not_menu_button))
async def got_pet_breed(message: Message, state: FSMContext, business: BusinessConfig) -> None:
    try:
        breed = pets_service.parse_pet_breed(message.text)
    except pets_service.PetInputError:
        await message.answer(texts.INVALID_PET_BREED, reply_markup=kb.back_kb())
        return
    await state.update_data(new_pet_breed=breed)
    await _ask_size(message, state, business)


@router.callback_query(BookingStates.pet_size, PetOptionCb.filter(F.kind == "size"))
async def got_pet_size(
    callback: CallbackQuery, callback_data: PetOptionCb, state: FSMContext,
    session: AsyncSession, business: BusinessConfig,
) -> None:
    if callback_data.key not in {s.key for s in business.pets.sizes}:
        await _ask_size(callback, state, business)
        return
    data = await state.get_data()
    client = await booking_service.get_or_create_client(
        session, callback.from_user.id, callback.from_user.full_name
    )
    pet = await pets_service.create_pet(
        session, client, name=data["new_pet_name"], species=data["new_pet_species"],
        breed=data["new_pet_breed"], size=callback_data.key,
    )
    await _use_pet(callback, pet, state, session, business)


@router.callback_query(BookingStates.pet, NavCb.filter(F.action == "back"))
async def back_from_pet(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.pet_name, NavCb.filter(F.action == "back"))
async def back_from_pet_name(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if await pets_service.client_pets(session, callback.from_user.id):
        await show_pets(callback, state, session, business)
        return
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.pet_species, NavCb.filter(F.action == "back"))
async def back_from_pet_species(callback: CallbackQuery, state: FSMContext) -> None:
    await ask_pet_name(callback, state)


@router.callback_query(BookingStates.pet_breed, NavCb.filter(F.action == "back"))
async def back_from_pet_breed(
    callback: CallbackQuery, state: FSMContext, business: BusinessConfig
) -> None:
    await _ask_species(callback, state, business)


@router.callback_query(BookingStates.pet_size, NavCb.filter(F.action == "back"))
async def back_from_pet_size(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_breed(callback, state)


# --- Назад --------------------------------------------------------------------


@router.callback_query(BookingStates.service, NavCb.filter(F.action == "back"))
async def back_from_service(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    if business.pets is not None:
        await show_pets(callback, state, session, business)
        return
    await state.clear()
    await callback.message.edit_text(texts.BACK_TO_MENU)


@router.callback_query(BookingStates.master, NavCb.filter(F.action == "back"))
async def back_from_master(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_services(callback, state, session, business)


@router.callback_query(BookingStates.date, NavCb.filter(F.action == "back"))
async def back_from_date(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_masters(callback, state, session, business)


@router.callback_query(
    StateFilter(BookingStates.time, RescheduleStates.time), NavCb.filter(F.action == "back")
)
async def back_from_time(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_dates(callback, state, session, business)


@router.callback_query(BookingStates.confirm, NavCb.filter(F.action == "back"))
async def back_from_confirm(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, business: BusinessConfig
) -> None:
    await show_times(callback, state, session, business)
