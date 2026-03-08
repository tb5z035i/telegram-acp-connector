from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


class GatewaySettings(CommonSettings):
    gateway_host: str = Field(default="0.0.0.0", alias="GATEWAY_HOST")
    gateway_port: int = Field(default=8000, alias="GATEWAY_PORT")
    gateway_database_path: Path = Field(
        default=Path("./data/gateway.db"), alias="GATEWAY_DATABASE_PATH"
    )
    gateway_shared_token: str = Field(alias="GATEWAY_SHARED_TOKEN")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: int = Field(alias="TELEGRAM_ALLOWED_USER_ID")
    telegram_chat_id: int | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    telegram_webhook_url: str | None = Field(default=None, alias="TELEGRAM_WEBHOOK_URL")
    telegram_webhook_secret: str | None = Field(default=None, alias="TELEGRAM_WEBHOOK_SECRET")

    @field_validator("gateway_database_path")
    @classmethod
    def _expand_database_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class ConnectorSettings(CommonSettings):
    connector_gateway_url: str = Field(alias="CONNECTOR_GATEWAY_URL")
    connector_gateway_shared_token: str = Field(alias="CONNECTOR_GATEWAY_SHARED_TOKEN")
    connector_workdir: Path = Field(default=Path("."), alias="CONNECTOR_WORKDIR")
    connector_state_dir: Path = Field(
        default=Path(".telegram-acp-connector"), alias="CONNECTOR_STATE_DIR"
    )
    connector_alias: str | None = Field(default=None, alias="CONNECTOR_ALIAS")
    connector_agent_id: str | None = Field(default=None, alias="CONNECTOR_AGENT_ID")
    connector_default_acp_command: str | None = Field(
        default=None, alias="CONNECTOR_DEFAULT_ACP_COMMAND"
    )
    connector_heartbeat_seconds: float = Field(default=15.0, alias="CONNECTOR_HEARTBEAT_SECONDS")

    @field_validator("connector_workdir")
    @classmethod
    def _expand_workdir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("connector_state_dir")
    @classmethod
    def _expand_state_dir(cls, value: Path, info) -> Path:
        workdir = info.data.get("connector_workdir")
        if value.is_absolute():
            return value.expanduser().resolve()
        if workdir is None:
            return value.expanduser().resolve()
        return (workdir / value).expanduser().resolve()


def load_gateway_settings(env_file: Path | None = None) -> GatewaySettings:
    return GatewaySettings(_env_file=env_file)


def load_connector_settings(env_file: Path | None = None) -> ConnectorSettings:
    return ConnectorSettings(_env_file=env_file)
