from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    pet = State()  # вибір збереженої тварини
    pet_name = State()
    pet_species = State()
    pet_breed = State()
    pet_weight = State()
    service = State()
    addons = State()  # додаткові послуги
    master = State()
    date = State()
    time = State()
    phone = State()
    confirm = State()


class SpaStates(StatesGroup):
    category = State()  # розділ: програми, масаж, відпочинок…
    service = State()  # послуга в розділі
    card = State()  # картка послуги: опис, варіанти тривалості
    guests = State()
    addons = State()
    event_date = State()  # дата групової події
    cert = State()  # сертифікат: на послугу чи на суму
    cert_category = State()
    cert_service = State()
    cert_amount = State()
    cert_confirm = State()
    cert_phone = State()


class RescheduleStates(StatesGroup):
    date = State()
    time = State()
    confirm = State()


class AdminStates(StatesGroup):
    input = State()  # очікуємо текст; що саме — у data["kind"]
    confirm = State()  # чекаємо підтвердження дії з конфліктами
