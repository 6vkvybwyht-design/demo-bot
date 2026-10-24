from aiogram.filters.callback_data import CallbackData

ANY_MASTER = 0  # master_id для «будь-який майстер»


class ServiceCb(CallbackData, prefix="svc"):
    service_id: int


NEW_PET = 0  # pet_id для «Інша тварина»


class PetCb(CallbackData, prefix="pet"):
    pet_id: int


class PetOptionCb(CallbackData, prefix="petopt"):
    kind: str  # species / breed / weight
    key: str


class AddonCb(CallbackData, prefix="addon"):
    service_id: int


class MasterCb(CallbackData, prefix="mst"):
    master_id: int


class DateCb(CallbackData, prefix="day"):
    day: str  # ISO-дата за часовим поясом бізнесу


class TimeCb(CallbackData, prefix="tm"):
    ts: int  # Unix-час початку слоту (UTC)


class NavCb(CallbackData, prefix="nav"):
    action: str  # back / confirm


class NoopCb(CallbackData, prefix="noop"):
    """Кнопка-заголовок без дії (наприклад, «Ранок» над слотами)."""


class MyBookingCb(CallbackData, prefix="my"):
    action: str  # cancel / cancel_yes / cancel_no / move / attend / pay
    booking_id: int


class AdminCb(CallbackData, prefix="adm"):
    action: str
    id: int = 0
    arg: str = ""


# --- Спа ----------------------------------------------------------------------


class CategoryCb(CallbackData, prefix="cat"):
    key: str  # ключ розділу з spa.categories


class SpaCardCb(CallbackData, prefix="spa"):
    action: str  # more / less / pick
    variant: int = 0  # номер варіанта тривалості (pick)


class GuestCb(CallbackData, prefix="guest"):
    kind: str  # ключ виду гостей
    delta: int  # +1 / -1


class EventDateCb(CallbackData, prefix="evt"):
    ts: int  # Unix-час початку події (UTC)


class CertCb(CallbackData, prefix="cert"):
    action: str  # service / amount / sum
    amount: int = 0
