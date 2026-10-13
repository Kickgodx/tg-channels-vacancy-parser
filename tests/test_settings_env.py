"""APP_ENV: dev-умолчания удобны локально, prod — закрытые; явный env побеждает в обоих."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.settings import Settings

_PROD_SECRET = "x" * 32
_ENV_KEYS = ("APP_ENV", "JWT_SECRET", "AUTH_REGISTRATION_OPEN", "HTTP_LOG_BODIES")


@pytest.fixture
def make_settings(monkeypatch):
    """Settings только из переданных переменных: без .env разработчика и окружения."""

    def _make(**env: str) -> Settings:
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return _make


def test_dev_is_default_and_keeps_local_conveniences(make_settings):
    s = make_settings()
    assert s.app_env == "dev"
    assert s.is_production is False
    assert s.auth_registration_open is True
    assert s.http_log_bodies is True
    assert s.jwt_secret is None  # эфемерный секрет — только в dev


def test_prod_closes_defaults(make_settings):
    s = make_settings(APP_ENV="prod", JWT_SECRET=_PROD_SECRET)
    assert s.is_production is True
    assert s.auth_registration_open is False
    assert s.http_log_bodies is False


@pytest.mark.parametrize("secret", [None, "", "   ", "short-secret", "x" * 31])
def test_prod_requires_long_jwt_secret(make_settings, secret):
    env = {"APP_ENV": "prod"}
    if secret is not None:
        env["JWT_SECRET"] = secret
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(**env)


def test_startup_error_does_not_echo_env_values(make_settings):
    with pytest.raises(ValidationError) as exc:
        make_settings(APP_ENV="prod", JWT_SECRET="short-secret")
    assert "short-secret" not in str(exc.value)


def test_explicit_values_win_in_prod(make_settings):
    s = make_settings(
        APP_ENV="prod",
        JWT_SECRET=_PROD_SECRET,
        AUTH_REGISTRATION_OPEN="true",
        HTTP_LOG_BODIES="true",
    )
    assert s.auth_registration_open is True
    assert s.http_log_bodies is True


def test_explicit_values_win_in_dev(make_settings):
    s = make_settings(AUTH_REGISTRATION_OPEN="false", HTTP_LOG_BODIES="false")
    assert s.auth_registration_open is False
    assert s.http_log_bodies is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "dev"),
        ("DEV", "dev"),
        ("development", "dev"),
        ("local", "dev"),
        (" Prod ", "prod"),
        ("production", "prod"),
    ],
)
def test_app_env_aliases(make_settings, raw, expected):
    assert make_settings(APP_ENV=raw, JWT_SECRET=_PROD_SECRET).app_env == expected


def test_unknown_app_env_is_rejected(make_settings):
    # Опечатка не должна молча включать dev-умолчания.
    with pytest.raises(ValidationError):
        make_settings(APP_ENV="staging", JWT_SECRET=_PROD_SECRET)
