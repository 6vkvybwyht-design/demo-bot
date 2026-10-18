"""Профілі бізнесу: вибір через BUSINESS_PROFILE, тексти, перевірки YAML."""

import os
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from bot import texts
from bot.config import (
    BASE_DIR,
    PROFILES_DIR,
    BaseAppSettings,
    BusinessConfig,
    Settings,
    list_profiles,
    load_business_config,
    profile_path,
)
from bot.db.session import create_session_factory
from bot.keyboards.callbacks import AdminCb
from bot.main import create_dispatcher
from tests.conftest import ADMIN_ID
from tests.fake_telegram import TelegramUser


def test_default_profile_is_barber(monkeypatch):
    monkeypatch.delenv("BUSINESS_PROFILE", raising=False)
    settings = BaseAppSettings(_env_file=None)
    assert settings.business_profile == "barber"
    assert settings.business_config_path == PROFILES_DIR / "barber.yaml"
    assert load_business_config(settings.business_config_path).business.name == "Demo Barber"


def test_profile_from_env(monkeypatch):
    monkeypatch.setenv("BUSINESS_PROFILE", "nails")
    settings = BaseAppSettings(_env_file=None)
    business = load_business_config(settings.business_config_path)
    assert business.business.name == "Demo Nails"
    assert business.business.address == "м. Львів, вул. Городоцька, 1"


def test_empty_profile_means_default(monkeypatch):
    monkeypatch.setenv("BUSINESS_PROFILE", "  ")
    assert BaseAppSettings(_env_file=None).business_profile == "barber"


@pytest.mark.parametrize("name", ["../secrets", "Nails", "nails.yaml", "a b"])
def test_invalid_profile_name(monkeypatch, name):
    monkeypatch.setenv("BUSINESS_PROFILE", name)
    with pytest.raises(ValidationError, match="BUSINESS_PROFILE"):
        BaseAppSettings(_env_file=None)


def test_missing_profile_lists_available():
    with pytest.raises(FileNotFoundError, match="Доступні: barber, nails"):
        profile_path("yoga")


@pytest.mark.parametrize("name", ["barber", "nails"])
def test_profiles_load_with_demo_and_valid_texts(name):
    business = load_business_config(profile_path(name))
    assert business.demo is not None and business.demo.services
    texts.apply_overrides(business.texts)  # без помилок


def test_list_profiles():
    assert {"barber", "nails"} <= set(list_profiles())


# --- Тексти -------------------------------------------------------------------


def test_texts_are_niche_neutral_by_default():
    for name, value in texts._DEFAULTS.items():
        if isinstance(value, str):
            for word in ("барбер", "стрижк", "Barber", "манікюр", "💈", "💅"):
                assert word.lower() not in value.lower(), (name, word)


def test_barber_texts_unchanged():
    texts.apply_overrides(load_business_config(profile_path("barber")).texts)
    assert texts.BTN_ADMIN_SERVICES == "💈 Послуги"
    assert texts.SUMMARY.startswith("💈 Послуга: <b>{service}</b>")
    assert texts.ADMIN_SERVICE.startswith("💈 <b>{name}</b>")


def test_nails_texts():
    texts.apply_overrides(load_business_config(profile_path("nails")).texts)
    derived = [getattr(texts, name) for name in texts._ICON_DERIVED]
    assert all("💅" in value and "💈" not in value for value in derived)
    assert len(derived) == 7


def test_apply_overrides_resets_previous_profile():
    texts.apply_overrides({"SERVICE_ICON": "💅", "WELCOME": "Вітаємо у {name}!"})
    texts.apply_overrides({})
    assert texts.WELCOME == texts._DEFAULTS["WELCOME"]
    assert "💅" not in texts.BTN_ADMIN_SERVICES


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"WELCOM": "Привіт"}, "WELCOM: такого тексту немає"),
        ({"WELCOME": "Привіт без назви"}, "WELCOME: потрібні підстановки {name}"),
        ({"STATUS_ICONS": "x"}, "STATUS_ICONS: такого тексту немає"),  # не рядок
    ],
)
def test_invalid_text_overrides(overrides, message):
    with pytest.raises(ValueError, match=message):
        texts.apply_overrides(overrides)


async def test_dispatcher_applies_profile_texts(engine):
    nails = load_business_config(profile_path("nails"))
    settings = Settings(_env_file=None, bot_token="42:TEST", admin_ids=[ADMIN_ID])
    dp = create_dispatcher(settings, nails, create_session_factory(engine))
    owner = TelegramUser(dp, user_id=ADMIN_ID)
    await owner.click(AdminCb(action="menu").pack())
    assert "💅 Послуги" in [label for label, _ in owner.buttons()]


# --- Перевірки YAML профілю ---------------------------------------------------


def _barber_data() -> dict:
    return yaml.safe_load(profile_path("barber").read_text(encoding="utf-8"))


def test_time_without_quotes_is_rejected():
    data = _barber_data()
    raw = yaml.safe_load("start: 10:00")  # так YAML читає час без лапок
    assert raw["start"] == 600
    data["demo"]["schedule"]["start"] = raw["start"]
    with pytest.raises(ValidationError, match="у лапках"):
        BusinessConfig.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("masters", 0, "services"), ["haircut", "manicure"], "невідомі ключі послуг: manicure"),
        (("booking_patterns", 0, 0, "service"), "yoga", "невідомі ключі послуг: yoga"),
        (("time_off", 0, "master"), "Петро", "невідомі майстри: Петро"),
        (("schedule", "break_start"), "21:00", "перерва"),
        (("schedule", "weekdays"), [0, 7], "дні тижня"),
    ],
)
def test_demo_section_validation(path, value, message):
    data = _barber_data()
    target = data["demo"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        BusinessConfig.model_validate(data)


def test_seed_command_uses_selected_profile(tmp_path):
    env = {
        **os.environ,
        "BUSINESS_PROFILE": "nails",
        "DEMO_MODE": "true",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'nails.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "seed.demo_data"], cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "послуг 7, майстрів 3, клієнтів 5" in result.stdout


def test_bad_env_is_logged_to_file(tmp_path):
    """Помилка в .env потрапляє в лог-файл — на Windows інакше не видно причини."""
    log = tmp_path / "bot.log"
    env = {**os.environ, "BUSINESS_PROFILE": "../etc", "LOG_FILE": str(log), "BOT_TOKEN": "1:a"}
    result = subprocess.run(
        [sys.executable, "-m", "bot.main"], cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "BUSINESS_PROFILE" in log.read_text(encoding="utf-8")
