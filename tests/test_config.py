import pytest
from pydantic import ValidationError

from bot.config import DEFAULT_BUSINESS_CONFIG, BookingRules, Settings, load_business_config


def test_business_yaml_loads():
    cfg = load_business_config(DEFAULT_BUSINESS_CONFIG)
    assert cfg.business.name == "Demo Barber"
    assert cfg.business.address == "м. Львів, пл. Ринок, 1"
    assert cfg.booking.timezone == "Europe/Kyiv"
    assert cfg.booking.slot_step_min == 15
    assert cfg.booking.horizon_days == 14
    assert cfg.booking.min_lead_time_min == 60
    assert cfg.booking.cancel_deadline_hours == 2
    assert cfg.reminders.before_24h and cfg.reminders.before_2h
    assert cfg.booking.tz.key == "Europe/Kyiv"


def test_invalid_timezone_rejected():
    with pytest.raises(ValidationError):
        BookingRules(timezone="Mars/Olympus")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1, 2,3", [1, 2, 3]), ("[4, 5]", [4, 5]), ("", [])],
)
def test_admin_ids_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_IDS", raw)
    settings = Settings(_env_file=None)
    assert settings.admin_ids == expected


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.bot_token.get_secret_value() == "123:abc"
    assert settings.demo_mode is True
    assert settings.admin_ids == []
