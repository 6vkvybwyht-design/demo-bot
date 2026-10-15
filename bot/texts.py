"""Усі тексти бота. Єдине місце для ребрендингу й правок формулювань."""

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
    "💈 Послуга: <b>{service}</b>\n"
    "👤 Майстер: {master}\n"
    "📅 Дата: {date}\n"
    "🕐 Час: {time}\n"
    "⏱ Тривалість: {duration} хв\n"
    "💵 Вартість: {price} грн"
)
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
BTN_ADMIN_SERVICES = "💈 Послуги"
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
}
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
ADMIN_SERVICES = "💈 <b>Послуги</b>\n\n🙈 — приховані від клієнтів."
ADMIN_SERVICE_BUTTON = "{hidden}{name} · {duration} хв · {price} грн"
HIDDEN_MARK = "🙈 "
BTN_ADD_SERVICE = "➕ Додати послугу"
ADMIN_SERVICE = "💈 <b>{name}</b>\n⏱ {duration} хв · 💵 {price} грн\nСтатус: {status}"
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
    "💈 Послуги: {services}\n\n🕐 Графік:\n{schedule}"
)
NOT_SET = "не вказано"
NO_SERVICES_LINKED = "не прив'язано"
BTN_EDIT_MASTER_NAME = "✏️ Ім'я"
BTN_EDIT_TELEGRAM_ID = "🔔 Telegram ID"
BTN_MASTER_SERVICES = "💈 Послуги майстра"
BTN_BACK_TO_MASTERS = "⬅️ До майстрів"
BTN_BACK_TO_MASTER = "⬅️ До майстра"
ASK_MASTER_NAME = "Введіть ім'я майстра:"
ASK_TELEGRAM_ID = (
    "Введіть числовий Telegram ID майстра, щоб він отримував сповіщення "
    "(дізнатися ID можна, наприклад, через @userinfobot), або «-», щоб прибрати:"
)
HINT_TELEGRAM_ID = "Потрібне число, наприклад 123456789, або «-»."
ADMIN_MASTER_SERVICES = "💈 Послуги майстра «{name}»\n\nНатисніть, щоб прив'язати ✅ або відв'язати ▫️:"
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
