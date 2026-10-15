from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    service = State()
    master = State()
    date = State()
    time = State()
    phone = State()
    confirm = State()
