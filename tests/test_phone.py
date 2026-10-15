import pytest

from bot.services.phone import normalize_contact_phone, parse_ua_phone


@pytest.mark.parametrize(
    "raw",
    [
        "0501234567",
        "050 123 45 67",
        "050-123-45-67",
        "(050) 123-45-67",
        "380501234567",
        "+380501234567",
        "+38 (050) 123 45 67",
        "  +380 50 123 4567  ",
    ],
)
def test_valid_ua_numbers_normalized(raw):
    assert parse_ua_phone(raw) == "+380501234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0322000000", "+380322000000"),  # стаціонарний Львів
        ("0991112233", "+380991112233"),
        ("+380671112233", "+380671112233"),
    ],
)
def test_other_ua_codes(raw, expected):
    assert parse_ua_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "привіт",
        "050123456",  # закоротко
        "05012345678",  # задовго
        "+3805012345678",  # задовго
        "0001234567",  # код не може починатися з 0
        "0101234567",  # ні з 1
        "+48501234567",  # Польща
        "+1 202 555 0100",  # США
        "80501234567",  # старий формат 8-0XX не приймаємо
        "050 123 45 6x",
        "+380 50 123 45 67 доб. 2",
    ],
)
def test_invalid_numbers_rejected(raw):
    assert parse_ua_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("380501234567", "+380501234567"),  # Telegram часто надсилає без «+»
        ("+380501234567", "+380501234567"),
        ("48501234567", "+48501234567"),  # іноземний контакт приймаємо як є
    ],
)
def test_contact_phone(raw, expected):
    assert normalize_contact_phone(raw) == expected
