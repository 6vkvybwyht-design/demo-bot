"""Розбір і нормалізація телефонних номерів."""

import re

# Український номер: код країни 380 + 9 цифр, перша з яких — код оператора/міста (3–9)
_UA_NATIONAL = r"[3-9]\d{8}"
_UA_PATTERNS = (
    re.compile(rf"\+380({_UA_NATIONAL})"),  # +380501234567
    re.compile(rf"380({_UA_NATIONAL})"),  # 380501234567
    re.compile(rf"0({_UA_NATIONAL})"),  # 0501234567
)
# Роздільники, які люди зазвичай ставлять у номері
_SEPARATORS = re.compile(r"[\s\-()]")


def parse_ua_phone(text: str) -> str | None:
    """Номер, введений текстом, у форматі +380XXXXXXXXX або None, якщо це не український номер."""
    compact = _SEPARATORS.sub("", text.strip())
    for pattern in _UA_PATTERNS:
        match = pattern.fullmatch(compact)
        if match:
            return f"+380{match.group(1)}"
    return None


def normalize_contact_phone(phone: str) -> str:
    """Номер із контакту Telegram. Може бути іноземним, тому лише додаємо «+»."""
    parsed = parse_ua_phone(phone)
    if parsed:
        return parsed
    compact = _SEPARATORS.sub("", phone.strip())
    return compact if compact.startswith("+") else f"+{compact}"
