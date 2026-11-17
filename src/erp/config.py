"""Application settings.

Secret values are not fields. Settings hold references that a SecretsProvider
resolves. ``SHADOW_MODE`` defaults to true so a fresh worker cannot push stock
to a marketplace by accident.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


_YAML_TO_ENV = {
    "database_url": "DATABASE_URL",
    "redis_url": "REDIS_URL",
    "shadow_mode": "SHADOW_MODE",
    "secrets_provider": "SECRETS_PROVIDER",
    "ops_bearer_token_ref": "OPS_BEARER_TOKEN_REF",
    "webhook_hmac_secret_ref": "WEBHOOK_HMAC_SECRET_REF",
    "webhook_url": "WEBHOOK_URL",
    "log_level": "LOG_LEVEL",
}


def apply_config_file(path: str | None = None) -> None:
    """Fill unset environment variables from a YAML file of placeholders/refs.

    Existing environment variables win. Nested documentation keys (such as
    ``oci``) are ignored by the settings model.
    """

    config_path = path if path is not None else os.environ.get("CONFIG_FILE")
    if not config_path:
        return
    with open(config_path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError("CONFIG_FILE must contain a YAML mapping")
    for key, env_name in _YAML_TO_ENV.items():
        if env_name in os.environ or key not in loaded or loaded[key] is None:
            continue
        value = loaded[key]
        if isinstance(value, bool):
            os.environ[env_name] = "true" if value else "false"
        else:
            os.environ[env_name] = str(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    shadow_mode: bool = True
    secrets_provider: Literal["local", "oci_vault"] = "local"
    ops_bearer_token_ref: str
    webhook_hmac_secret_ref: str
    webhook_url: str = ""
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    apply_config_file()
    return Settings()
