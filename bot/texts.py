"""Усі тексти бота. Єдине місце для ребрендингу й правок формулювань.

Тексти тут нейтральні — без прив'язки до ніші. Те, що відрізняється між бізнесами
(емодзі послуг, привітання тощо), перевизначається в профілі бізнесу:
розділ `texts` у config/profiles/<профіль>.yaml (див. apply_overrides внизу).
"""

import string
from typing import Any

# Емодзі послуг (у барбершопі 💈, у манікюрному салоні 💅). Рядки, що його
# містять, перебудовуються, коли профіль змінює SERVICE_ICON.
SERVICE_ICON = "📋"

# Кнопки головного меню
BTN_BOOK = "📅 Записатися"
BTN_MY_BOOKINGS = "🗓 Мої записи"
BTN_CONTACTS = "📍 Контакти"

# /start
WELCOME = (
    "Вітаємо у <b>{name}</b>! 👋\n\n"
    "Тут можна записатися на послугу, переглянути свої записи "
    "або дізнатися наші контакти.\n\n"
    "Оберіть дію в меню нижче."
)
MENU_PLACEHOLDER = "Оберіть дію"

# Загальне
BTN_BACK = "⬅️ Назад"
BACK_TO_MENU = "Добре, повертаємось до меню."
STALE_BUTTON = "Ця кнопка вже неактуальна. Почніть заново з меню."
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")

# Запис із твариною (профілі з розділом pets): кличка → вид → порода → вага
CHOOSE_PET = "Для кого запис?"
PET_BUTTON = "🐾 {name} · {category}"
BTN_NEW_PET = "➕ Інша тварина"
ASK_PET_NAME = "Як звати вашого улюбленця? Напишіть кличку повідомленням."
ASK_PET_SPECIES = "<b>{name}</b> — це:"
ASK_PET_BREED = "Яка порода? Від неї залежать ціна й тривалість послуг:"
ASK_PET_WEIGHT = "Скільки важить <b>{name}</b>?"
INVALID_PET_NAME = "Кличка — від 1 до 30 символів. Напишіть ще раз:"
NO_SERVICES_FOR_PET = (
    "На жаль, для цієї тварини послуг онлайн немає. Зателефонуйте нам: {phone}"
)

# Додаткові послуги (можна обрати кілька)
ASK_ADDONS = (
    "Додати послуги до «{service}»? Можна обрати кілька — ціни й час додаються.\n"
    "Натисніть, щоб додати ✅ або прибрати ▫️, потім «Далі»."
)
ADDON_SELECTED = "✅ "
ADDON_UNSELECTED = "▫️ "
BTN_ADDONS_NEXT = "Далі ➡️"

# Спа (профілі з розділом spa): розділи, картка послуги, гості, групові події
BTN_CERTIFICATE = "🎁 Сертифікати"
BTN_RULES = "📜 Правила відвідування"
CHOOSE_CATEGORY = "Оберіть розділ:"
SPA_CARD = "<b>{name}</b>"
SPA_CARD_SHORT = "\n{text}"
SPA_CARD_FULL = "\n\n{text}"
SPA_CARD_PRICE = "\n\n⏱ {duration} хв · 💵 {price} грн"
SPA_CARD_DEPOSIT = "\n💳 Потрібен аванс {percent}% — бронювання підтверджується після оплати."
SPA_CARD_EVENT = "\n📅 {when}, до {capacity} місць"
SPA_CARD_VARIANTS = "\n\nОберіть тривалість:"
SPA_VARIANT_BUTTON = "{duration} хв · {price} грн"
BTN_SPA_MORE = "📖 Детальніше"
BTN_SPA_LESS = "⬆️ Коротше"
BTN_SPA_PICK = "✅ Обрати"
ASK_GUESTS = (
    "Скільки гостей?\n\n{lines}\n\n💵 Вартість: <b>{price} грн</b> · ⏱ {duration} хв\n\n"
    "Змінюйте кількість кнопками ➖ / ➕, потім «Далі»."
)
GUESTS_LINE = "• {label}: <b>{count}</b>"
GUESTS_FREE_SEATS = "\n\nВільних місць: {free}"
GUEST_BUTTON = "{label}: {count}"
BTN_MINUS = "➖"
BTN_PLUS = "➕"
GUESTS_LIMIT = "❗️ {error}"
ASK_SPA_ADDONS = (
    "Додаткові послуги до «{service}» — можна обрати кілька.\n"
    "Натисніть, щоб додати ✅ або прибрати ▫️, потім «Далі»."
)
SPA_ADDON_BUTTON = "{name} · {price} грн"
SPA_ADDON_EXTRA = " · +{minutes} хв"
CHOOSE_EVENT_DATE = "Оберіть дату:"
EVENT_DATE_BUTTON = "{date}, {time} · вільно {free}"
NO_EVENT_DATES = "На найближчі {days} днів вільних місць немає. Зателефонуйте нам: {phone}"
EVENT_FULL = "😔 На жаль, стільки вільних місць уже немає. Оберіть іншу дату:"

# Аванс: бронювання підтверджується лише після оплати
CONFIRM_DEPOSIT = "\n💳 Аванс {percent}%: <b>{amount} грн</b>"
CONFIRM_DEPOSIT_RULES = (
    "\n\n<b>Правила бронювання:</b>\n{rules}\n\n"
    "Після підтвердження час утримується {hold} хв. Без авансу бронювання скасується автоматично."
)
BOOKING_HELD = (
    "⏳ Час заброньовано до {until}.\n\n{summary}\n\n📍 {address}\n\n"
    "Бронювання підтвердиться після оплати авансу {amount} грн.{how}"
)
BOOKING_HELD_DEMO = " У демо оплата імітується кнопкою нижче."
BOOKING_HELD_MANUAL = " Адміністратор зв'яжеться з вами щодо оплати."
BTN_PAY_DEPOSIT = "💳 Оплатити аванс {amount} грн (ДЕМО)"
DEPOSIT_PAID = "✅ Аванс отримано{demo}. Бронювання підтверджено!\n\n{summary}\n\n📍 {address}\n\n{after}"
DEPOSIT_DEMO_MARK = " (ДЕМО)"
DEPOSIT_EXPIRED_CLIENT = (
    "⌛️ Бронювання скасовано: аванс не внесено вчасно.\n\n{summary}\n\n"
    "Щоб записатися знову, натисніть «📅 Записатися» або зателефонуйте нам: {phone}"
)

# Подарункові сертифікати й правила
CERT_START = "🎁 <b>Подарунковий сертифікат</b>\n\nНа послугу чи на суму?"
BTN_CERT_SERVICE = "На послугу"
BTN_CERT_AMOUNT = "На суму"
CERT_CHOOSE_AMOUNT = "Оберіть суму сертифіката:"
CERT_AMOUNT_BUTTON = "{amount} грн"
CERT_CHOOSE_SERVICE = "Сертифікат на послугу. Оберіть розділ:"
CERT_ITEM_SERVICE = "Послуга: {name}"
CERT_ITEM_AMOUNT = "Сума: {amount} грн"
CERT_CONFIRM = (
    "Надіслати заявку на сертифікат?\n\n🎁 {item}\n\n"
    "Адміністратор зв'яжеться з вами, щоб оформити й оплатити сертифікат."
)
BTN_CERT_SEND = "✅ Надіслати заявку"
CERT_ASK_PHONE = (
    "Залиште, будь ласка, номер телефону — адміністратор зв'яжеться з вами.\n\n"
    "Натисніть «📱 Поділитися контактом» нижче або введіть номер повідомленням."
)
CERT_SENT = "✅ Заявку надіслано! Адміністратор зв'яжеться з вами найближчим часом."
NOTIFY_CERTIFICATE = "🎁 <b>Заявка на подарунковий сертифікат</b>\n\n{item}\n\n🙋 Клієнт: {client}\n📞 {phone}"

# Запис: вибір послуги, майстра, дати й часу
CHOOSE_SERVICE = "Оберіть послугу:"
SERVICE_BUTTON = "{name} · {duration} хв · {price} грн"
NO_SERVICES = "Наразі запис недоступний. Зателефонуйте нам: {phone}"
CHOOSE_MASTER = "Оберіть майстра:"
BTN_ANY_MASTER = "👥 Будь-який майстер"
NO_MASTERS = "На жаль, цю послугу зараз ніхто не виконує. Оберіть іншу."
CHOOSE_DATE = "Оберіть дату:"
NO_DATES = (
    "На жаль, найближчі {days} днів вільного часу немає. "
    "Оберіть іншого майстра або зателефонуйте нам: {phone}"
)
CHOOSE_TIME = "Оберіть час:"
NO_TIMES = "На цю дату вільного часу вже немає. Оберіть іншу:"
SLOT_TAKEN = "😔 На жаль, цей час щойно зайняли. Оберіть інший:"

# Наростаючий підсумок над кожним кроком: послуга · майстер · дата · час
PROGRESS = "🧾 <b>{line}</b>\n\n{prompt}"
PROGRESS_SEPARATOR = " · "
PROGRESS_ANY_MASTER = "будь-який майстер"

# Частини дня на екрані вибору часу (заголовки-кнопки, без дії)
DAY_PART_MORNING = "🌅 Ранок"
DAY_PART_AFTERNOON = "☀️ День"
DAY_PART_EVENING = "🌙 Вечір"

# Запис: телефон
PHONE_STEP = "Майже готово — залишився номер телефону 👇"
BTN_SHARE_PHONE = "📱 Поділитися контактом"
ASK_PHONE = (
    "Залиште, будь ласка, номер телефону — щоб майстер міг зв'язатися з вами, "
    "якщо щось зміниться.\n\n"
    "Натисніть кнопку «📱 Поділитися контактом» нижче або введіть номер "
    "повідомленням. Запитуємо лише один раз."
)
PHONE_NOT_OWN = "Будь ласка, поділіться власним контактом кнопкою нижче або введіть номер."
PHONE_INVALID = (
    "Не вдалося розпізнати номер. Введіть український номер у форматі "
    "<code>0501234567</code> або <code>+380501234567</code>, "
    "або натисніть кнопку «📱 Поділитися контактом» нижче."
)
PHONE_SAVED = "Дякуємо, номер збережено."
BACK_TO_TIMES = "Повертаємось до вибору часу."

# Запис: підтвердження
BTN_CONFIRM = "✅ Підтвердити"
CONFIRM = "Перевірте, будь ласка, запис:\n\n{summary}"
ANY_MASTER_LABEL = "будь-який (призначимо автоматично)"
SUMMARY = (
    SERVICE_ICON + " Послуга: <b>{service}</b>\n"
    "👤 Майстер: {master}\n"
    "📅 Дата: {date}\n"
    "🕐 Час: {time}\n"
    "⏱ Тривалість: {duration} хв\n"
    "💵 Вартість: {price} грн"
)
# Те саме без майстра (відпочинок у термальній зоні, групова подія)
SUMMARY_NO_MASTER = (
    SERVICE_ICON + " Послуга: <b>{service}</b>\n"
    "📅 Дата: {date}\n"
    "🕐 Час: {time}\n"
    "⏱ Тривалість: {duration} хв\n"
    "💵 Вартість: {price} грн"
)
SUMMARY_GUESTS = "\n👥 {details}"
SUMMARY_DEPOSIT_PENDING = "\n💳 Аванс {amount} грн — очікуємо оплату до {until}"
SUMMARY_DEPOSIT_PAID = "\n💳 Аванс {amount} грн — внесено ✅"
SUMMARY_PET = "🐾 Тварина: <b>{name}</b> ({description})\n"
SUMMARY_ADDONS = "\n➕ Додатково: {names}"
PRICE_FROM = "від {price}"
PRICE_FROM_NOTE = "\nℹ️ Точна ціна — після огляду майстром."
BOOKING_DONE = "✅ Ви записані!\n\n{summary}\n\n📍 {address}\n\n{after}"
# Після запису. Нагадування залежать від конфігу й того, скільки часу до візиту
REMINDERS_BOTH = "Ми нагадаємо за день і за 2 години."
REMINDER_24H = "Ми нагадаємо за день."
REMINDER_2H = "Ми нагадаємо за 2 години."
MANAGE_BOOKING = "Скасувати чи перенести запис можна в «Мої записи»."

# Контакти
CONTACTS = "<b>{name}</b>\n\n📍 {address}\n📞 {phone}"
BTN_MAP = "🗺 Відкрити на карті"
BTN_INSTAGRAM = "📷 Instagram"

# Мої записи
MY_BOOKINGS_EMPTY = "У вас немає майбутніх записів. Щоб записатися, натисніть «📅 Записатися»."
MY_BOOKINGS_HEADER = "Ваші майбутні записи: {count}"
BTN_CANCEL_BOOKING = "❌ Скасувати"
BTN_MOVE_BOOKING = "🔁 Перенести"
CHANGE_DEADLINE_PASSED = (
    "Скасувати чи перенести запис у боті можна не пізніше ніж за {hours} год до візиту. "
    "Зателефонуйте нам: {phone}"
)
BOOKING_GONE = "Цей запис уже неактуальний: його скасовано або час минув."

# Скасування
CANCEL_CONFIRM = "Скасувати цей запис?\n\n{summary}"
BTN_CANCEL_YES = "Так, скасувати"
BTN_CANCEL_NO = "Ні, залишити"
BOOKING_CANCELLED = "❌ Запис скасовано.\n\n{summary}"

# Перенесення
MOVE_CHOOSE_DATE = "Перенесення запису. Оберіть нову дату:"
MOVE_CHOOSE_TIME = "Перенесення запису. Оберіть новий час:"
MOVE_CONFIRM = "Перенести запис?\n\nБуло: {old}\nСтане: <b>{new}</b>\n\n{summary}"
MOVE_WHEN = "{date}, {time}"
BTN_MOVE_CONFIRM = "✅ Перенести"
BOOKING_MOVED = "✅ Запис перенесено!\n\n{summary}\n\n📍 {address}\n\n{after}"

# Сповіщення власнику й майстру
NOTIFY_NEW = "🆕 <b>Новий запис</b>\n\n{details}"
NOTIFY_CANCELLED_BY_CLIENT = "❌ <b>Клієнт скасував запис</b>\n\n{details}"
NOTIFY_CANCELLED_BY_ADMIN = "❌ <b>Запис скасовано адміністратором</b>\n\n{details}"
NOTIFY_RESCHEDULED = "🔁 <b>Запис перенесено</b>\nБуло: {old}\n\n{details}"
NOTIFY_NEW_PENDING = "🆕 <b>Нове бронювання — очікує авансу</b>\n\n{details}"
NOTIFY_DEPOSIT_PAID = "💳 <b>Аванс отримано</b>\n\n{details}"
NOTIFY_EXPIRED = "⌛️ <b>Бронювання скасовано автоматично: аванс не внесено</b>\n\n{details}"
STAFF_DETAILS = "{summary}\n\n🙋 Клієнт: {client}\n📞 {phone}"
NO_PHONE = "телефон не вказано"

# Сповіщення клієнту
CLIENT_CANCELLED_BY_ADMIN = (
    "😔 На жаль, ваш запис скасовано.\n\n{summary}\n\n"
    "Вибачте за незручності. Щоб обрати інший час, натисніть «📅 Записатися» "
    "або зателефонуйте нам: {phone}"
)

# ============================== Адмінка ======================================

ADMIN_ONLY = "Ця команда доступна лише власнику."
ADMIN_NO_ACCESS = "Немає доступу."
ADMIN_MENU = "⚙️ <b>Адмінка</b>\n\nОберіть розділ:"
BTN_ADMIN_TODAY = "📅 Сьогодні"
BTN_ADMIN_WEEK = "🗓 Тиждень"
BTN_ADMIN_SERVICES = f"{SERVICE_ICON} Послуги"
BTN_ADMIN_MASTERS = "👤 Майстри"
BTN_ADMIN_SCHEDULE = "🕐 Графік"
BTN_ADMIN_TIME_OFF = "⛔ Блокування часу"
BTN_ADMIN_MENU = "⬅️ До меню адмінки"
BTN_CANCEL_INPUT = "✖️ Скасувати"
BTN_PREV_DAY = "◀️"
BTN_NEXT_DAY = "▶️"
WEEKDAYS_FULL = ("понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя")
INPUT_CANCELLED = "Скасовано."
INVALID_INPUT = "❗️ Не вдалося розпізнати. {hint}"

# Записи
ADMIN_DAY = "📅 <b>{date}</b> — записів: {count}\n{lines}"
ADMIN_DAY_EMPTY = "📅 <b>{date}</b>\n\nЗаписів немає."
ADMIN_DAY_MASTER = "\n<b>{master}</b>"
ADMIN_DAY_LINE = "{icon} {time} · {service} · {client}"
ADMIN_WEEK = "🗓 <b>Найближчі 7 днів</b>\n\nОберіть день:"
ADMIN_WEEK_DAY = "{date} · {count}"
ADMIN_BOOKING_BUTTON = "{time} {client} ({master})"
STATUS_ICONS = {"confirmed": "🕐", "completed": "✅", "no_show": "🚫"}
STATUS_LABELS = {
    "confirmed": "підтверджено",
    "completed": "відбувся",
    "no_show": "не прийшов",
    "cancelled_by_client": "скасовано клієнтом",
    "cancelled_by_admin": "скасовано адміністратором",
    "expired": "скасовано: аванс не внесено",
}
STATUS_ICON_DEPOSIT_PENDING = "⏳"
BTN_DEPOSIT_RECEIVED = "💳 Аванс отримано"
ADMIN_DEPOSIT_MARKED = "✅ Аванс позначено отриманим."
ADMIN_BOOKING = "{details}\n\nСтатус: <b>{status}</b>"
BTN_MARK_COMPLETED = "✅ Відбувся"
BTN_MARK_NO_SHOW = "🚫 Не прийшов"
BTN_ADMIN_CANCEL = "❌ Скасувати запис"
BTN_BACK_TO_DAY = "⬅️ До списку"
ADMIN_CANCEL_CONFIRM = "Скасувати цей запис? Клієнт отримає сповіщення.\n\n{details}"
BTN_ADMIN_CANCEL_YES = "Так, скасувати"
BTN_ADMIN_CANCEL_NO = "Ні"
ADMIN_CANCELLED = "❌ Запис скасовано. {client_note}\n\n{details}"
CLIENT_NOTIFIED = "Клієнта сповіщено."
CLIENT_NOT_NOTIFIED = (
    "⚠️ Не вдалося сповістити клієнта (можливо, він заблокував бота) — "
    "зателефонуйте йому: {phone}."
)
ADMIN_BOOKING_CHANGED = "Запис уже змінено або час минув — оновлено картку."

# Попередження про майбутні записи
FUTURE_BOOKING_LINE = "• {when} · {service} · {master} · {client}, {phone}"
FUTURE_BOOKINGS_MORE = "…і ще {count}"
WARN_HIDE_SERVICE = (
    "⚠️ На послугу «{name}» є майбутні записи: {count}.\n"
    "Після приховування нових записів на неї не буде, а ці записи залишаться. "
    "Зв'яжіться з клієнтами, якщо потрібно:\n\n{lines}"
)
WARN_HIDE_MASTER = (
    "⚠️ У майстра «{name}» є майбутні записи: {count}.\n"
    "Після приховування нових записів до нього не буде, а ці записи залишаться. "
    "Зв'яжіться з клієнтами, якщо потрібно:\n\n{lines}"
)
WARN_UNLINK_SERVICE = (
    "⚠️ У майстра «{master}» є майбутні записи на «{service}»: {count}.\n"
    "Після відв'язування нових записів не буде, а ці залишаться. "
    "Зв'яжіться з клієнтами, якщо потрібно:\n\n{lines}"
)
WARN_SCHEDULE = (
    "⚠️ Новий графік на {weekday} не охоплює майбутні записи: {count}.\n"
    "Записи залишаться — зв'яжіться з клієнтами, якщо потрібно:\n\n{lines}"
)
WARN_TIME_OFF = (
    "⚠️ Блокування перетинається з майбутніми записами: {count}.\n"
    "Записи залишаться — зв'яжіться з клієнтами, якщо потрібно:\n\n{lines}"
)
BTN_FORCE_HIDE = "Все одно приховати"
BTN_FORCE_UNLINK = "Все одно відв'язати"
BTN_FORCE_SAVE = "Все одно зберегти"
BTN_ABORT = "Скасувати"

# Послуги
ADMIN_SERVICES = f"{SERVICE_ICON} <b>Послуги</b>\n\n🙈 — приховані від клієнтів."
ADMIN_SERVICE_BUTTON = "{hidden}{name} · {duration} хв · {price} грн"
HIDDEN_MARK = "🙈 "
BTN_ADD_SERVICE = "➕ Додати послугу"
ADMIN_SERVICE = SERVICE_ICON + " <b>{name}</b>\n⏱ {duration} хв · 💵 {price} грн\nСтатус: {status}"
ADMIN_SERVICE_PRICES = (
    "\n\n🐾 <b>Ціни для тварин</b> (з профілю бізнесу){addon}:\n{lines}\n"
    "<i>Тривалість і ціна вище — базові, для послуг без прайсу.</i>"
)
ADMIN_SERVICE_ADDON = ", додаткова послуга"
ADMIN_SERVICE_PRICE_LINE = "• {category}: {duration} хв · {price} грн"
ADMIN_SERVICE_ALL_OF_SPECIES = "{species} (усі)"
ADMIN_SERVICE_NOT_IN_PRICES = (
    "\n\n⚠️ Цієї послуги немає в прайсі профілю — для всіх тварин "
    "діють тривалість і ціна вище."
)
ADMIN_DAY_PET = "{client} · 🐾 {pet}"
STATUS_VISIBLE = "показується клієнтам"
STATUS_HIDDEN = "🙈 приховано"
BTN_EDIT_NAME = "✏️ Назва"
BTN_EDIT_DURATION = "⏱ Тривалість"
BTN_EDIT_PRICE = "💵 Ціна"
BTN_HIDE = "🙈 Приховати"
BTN_SHOW = "👁 Показати"
BTN_BACK_TO_SERVICES = "⬅️ До послуг"
ASK_SERVICE_NAME = "Введіть назву послуги:"
ASK_SERVICE_DURATION = "Тривалість у хвилинах (від 5 до 600):"
ASK_SERVICE_PRICE = "Ціна в гривнях (ціле число):"
HINT_NAME = "Введіть назву до 100 символів."
HINT_DURATION = "Введіть ціле число хвилин від 5 до 600."
HINT_PRICE = "Введіть ціну цілим числом гривень, наприклад 500."

# Майстри
ADMIN_MASTERS = "👤 <b>Майстри</b>\n\n🙈 — приховані від клієнтів."
BTN_ADD_MASTER = "➕ Додати майстра"
ADMIN_MASTER = (
    "👤 <b>{name}</b>\nСтатус: {status}\n🔔 Telegram ID: {telegram_id}\n"
    + SERVICE_ICON + " Послуги: {services}\n\n🕐 Графік:\n{schedule}"
)
NOT_SET = "не вказано"
NO_SERVICES_LINKED = "не прив'язано"
BTN_EDIT_MASTER_NAME = "✏️ Ім'я"
BTN_EDIT_TELEGRAM_ID = "🔔 Telegram ID"
BTN_MASTER_SERVICES = f"{SERVICE_ICON} Послуги майстра"
BTN_BACK_TO_MASTERS = "⬅️ До майстрів"
BTN_BACK_TO_MASTER = "⬅️ До майстра"
ASK_MASTER_NAME = "Введіть ім'я майстра:"
ASK_TELEGRAM_ID = (
    "Введіть числовий Telegram ID майстра, щоб він отримував сповіщення "
    "(дізнатися ID можна, наприклад, через @userinfobot), або «-», щоб прибрати:"
)
HINT_TELEGRAM_ID = "Потрібне число, наприклад 123456789, або «-»."
ADMIN_MASTER_SERVICES = (
    SERVICE_ICON + " Послуги майстра «{name}»\n\nНатисніть, щоб прив'язати ✅ або відв'язати ▫️:"
)
LINKED_MARK = "✅ "
UNLINKED_MARK = "▫️ "

# Графік
ADMIN_CHOOSE_MASTER = "Оберіть майстра:"
ADMIN_SCHEDULE = "🕐 <b>Графік: {name}</b>\n\nОберіть день, щоб змінити:"
SCHEDULE_DAY = "{weekday}: {hours}"
SCHEDULE_HOURS = "{start}–{end}"
SCHEDULE_BREAK = " (перерва {start}–{end})"
SCHEDULE_DAY_OFF = "вихідний"
ASK_HOURS = (
    "Робочі години на {weekday}:\n"
    "• «10:00-20:00»\n• з перервою — «10:00-20:00 14:00-15:00»\n• «вихідний»"
)
HINT_HOURS = "Приклади: «10:00-20:00», «10:00-20:00 14:00-15:00», «вихідний»."
SCHEDULE_SAVED = "✅ Графік збережено."

# Блокування часу
ASK_PERIOD = (
    "Період блокування для «{name}»:\n"
    "• «20.10 10:00-13:00» — частина дня\n• «20.10» — увесь день\n• «20.10-25.10» — кілька днів"
)
HINT_PERIOD = "Приклади: «20.10 10:00-13:00», «20.10», «20.10-25.10» (не довше 2 місяців)."
ASK_REASON = "Причина (відпустка, лікарняний тощо) або натисніть «Без причини»:"
BTN_NO_REASON = "Без причини"
TIME_OFF_SAVED = "✅ Час заблоковано: {period}."
ADMIN_TIME_OFFS = "⛔ <b>Блокування часу: {name}</b>\n\n{lines}"
TIME_OFFS_EMPTY = "Майбутніх блокувань немає."
TIME_OFF_LINE = "• {period}{reason}"
TIME_OFF_REASON = " — {reason}"
BTN_ADD_TIME_OFF = "➕ Додати блокування"
BTN_DELETE_TIME_OFF = "🗑 {period}"
TIME_OFF_DELETE_CONFIRM = (
    "Видалити блокування?\n\n{line}\n\nЦей час знову стане доступним для запису."
)
BTN_DELETE_YES = "Так, видалити"
BTN_DELETE_NO = "Ні"
TIME_OFF_DELETED = "🗑 Блокування видалено."
TIME_OFF_GONE = "Це блокування вже видалено."
PERIOD_FORMAT = "{start} — {end}"

# Нагадування
REMINDER = "⏰ <b>Нагадуємо про ваш запис: {when}</b>\n\n{summary}\n\n📍 {address}"
BTN_WILL_COME = "Буду ✅"
REMINDER_CANCEL_DEADLINE = (
    "\n\nЯкщо плани змінилися, зателефонуйте нам: {phone}"
)
ATTENDANCE_CONFIRMED = "👍 Дякуємо! Чекаємо на вас."
ADMIN_CLIENT_CONFIRMED = "👍 Клієнт підтвердив, що прийде"
STATUS_ICON_CLIENT_CONFIRMED = "👍"

# Демо-режим
BTN_DEMO_OWNER = "👀 Подивитись як власник"
DEMO_OWNER_INTRO = (
    "👀 <b>Демо: так бот бачить власник салону.</b>\n\n"
    "Дані тестові й щоночі о 04:00 скидаються — можна сміливо натискати все. "
    "Телефони й імена інших відвідувачів приховано; свої записи ви бачите повністю."
)
DEMO_CLIENT_NOT_NOTIFIED = "У демо-режимі сповіщення отримують лише ваші власні записи — клієнт нічого не отримав."
DEMO_OWNER_VIEW = "👀 <b>Так це побачить власник салону:</b>\n\n{notification}"
DEMO_NAMES_DISABLED = (
    "У демо зміни назв вимкнені. "
    "У вашому боті ви зможете додавати й редагувати майстрів і послуги тут."
)


# --- Перевизначення з профілю бізнесу -------------------------------------------

_DEFAULTS: dict[str, Any] = {
    name: value for name, value in globals().items() if name.isupper() and not name.startswith("_")
}
# Рядки, побудовані з SERVICE_ICON: при заміні іконки їх перебудовуємо
_ICON_DERIVED = {
    name
    for name, value in _DEFAULTS.items()
    if name != "SERVICE_ICON" and isinstance(value, str) and SERVICE_ICON in value
}


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}


def reset() -> None:
    """Повертає тексти за замовчуванням."""
    globals().update(_DEFAULTS)


def apply_overrides(overrides: dict[str, str]) -> None:
    """Застосовує тексти профілю поверх текстів за замовчуванням.

    Помилки (невідома назва, інші {підстановки}) — одразу на старті бота,
    а не тоді, коли клієнт натисне кнопку.
    """
    reset()
    errors = []
    for name, value in overrides.items():
        default = _DEFAULTS.get(name)
        if not isinstance(default, str):
            errors.append(f"{name}: такого тексту немає в bot/texts.py")
        elif _placeholders(value) != _placeholders(default):
            expected = ", ".join(sorted(_placeholders(default))) or "без підстановок"
            errors.append(f"{name}: потрібні підстановки {{{expected}}}")
    if errors:
        raise ValueError("Помилки в розділі texts профілю:\n" + "\n".join(errors))

    icon = overrides.get("SERVICE_ICON", SERVICE_ICON)
    for name in _ICON_DERIVED:
        globals()[name] = _DEFAULTS[name].replace(_DEFAULTS["SERVICE_ICON"], icon)
    globals().update(overrides)
