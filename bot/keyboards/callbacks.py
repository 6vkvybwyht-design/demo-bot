from aiogram.filters.callback_data import CallbackData

ANY_MASTER = 0  # master_id для «будь-який майстер»


class ServiceCb(CallbackData, prefix="svc"):
    service_id: int


class MasterCb(CallbackData, prefix="mst"):
    master_id: int


class DateCb(CallbackData, prefix="day"):
    day: str  # ISO-дата за часовим поясом бізнесу


class TimeCb(CallbackData, prefix="tm"):
    ts: int  # Unix-час початку слоту (UTC)


class NavCb(CallbackData, prefix="nav"):
    action: str  # back / confirm
