from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from telegram_acp_connector.config import load_connector_settings, load_gateway_settings
from telegram_acp_connector.connector.runtime import ConnectorRuntime, parse_acp_command
from telegram_acp_connector.gateway.app import create_app
from telegram_acp_connector.logging import configure_logging

main_app = typer.Typer(help="Telegram ACP connector")
connector_app = typer.Typer(
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Run the edge connector and own an ACP subprocess.",
)
gateway_app = typer.Typer(add_completion=False, help="Run the gateway service.")
ENV_FILE_OPTION = typer.Option(None, help="Optional env file to load settings from.")


@gateway_app.callback(invoke_without_command=True)
def gateway_main(
    env_file: Annotated[Path | None, ENV_FILE_OPTION] = None,
) -> None:
    settings = load_gateway_settings(env_file)
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.gateway_host,
        port=settings.gateway_port,
    )


@connector_app.callback(invoke_without_command=True)
def connector_main(
    ctx: typer.Context,
    env_file: Annotated[Path | None, ENV_FILE_OPTION] = None,
) -> None:
    settings = load_connector_settings(env_file)
    configure_logging(settings.log_level)
    acp_command = parse_acp_command(
        settings.connector_default_acp_command,
        list(ctx.args),
    )
    runtime = ConnectorRuntime(settings=settings, acp_command=acp_command)
    asyncio.run(runtime.run_forever())


@main_app.command("gateway")
def main_gateway(
    env_file: Annotated[Path | None, ENV_FILE_OPTION] = None,
) -> None:
    gateway_main(env_file)


@main_app.command(
    "connector",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def main_connector(
    ctx: typer.Context,
    env_file: Annotated[Path | None, ENV_FILE_OPTION] = None,
) -> None:
    settings = load_connector_settings(env_file)
    configure_logging(settings.log_level)
    acp_command = parse_acp_command(
        settings.connector_default_acp_command,
        list(ctx.args),
    )
    runtime = ConnectorRuntime(settings=settings, acp_command=acp_command)
    asyncio.run(runtime.run_forever())


def main() -> None:
    main_app()
