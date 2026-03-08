from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import typer
import uvicorn

from telegram_acp_connector.config import load_connector_settings, load_gateway_settings
from telegram_acp_connector.connector.runtime import ConnectorRuntime, parse_acp_command
from telegram_acp_connector.gateway.app import create_app
from telegram_acp_connector.logging import configure_logging

main_app = typer.Typer(help="Telegram ACP connector")


def _run_gateway(env_file: Path | None) -> None:
    settings = load_gateway_settings(env_file)
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.gateway_host,
        port=settings.gateway_port,
    )


def _run_connector(env_file: Path | None, passthrough_args: list[str]) -> None:
    settings = load_connector_settings(env_file)
    configure_logging(settings.log_level)
    acp_command = parse_acp_command(
        settings.connector_default_acp_command,
        passthrough_args,
    )
    runtime = ConnectorRuntime(settings=settings, acp_command=acp_command)
    asyncio.run(runtime.run_forever())


@main_app.command("gateway")
def gateway_main(
    env_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--env-file",
        help="Optional env file to load settings from.",
    ),
) -> None:
    _run_gateway(env_file)


@main_app.command(
    "connector",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def connector_main(
    ctx: typer.Context,
    env_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--env-file",
        help="Optional env file to load settings from.",
    ),
) -> None:
    _run_connector(env_file, list(ctx.args))


def run_gateway_cli() -> None:
    parser = argparse.ArgumentParser(prog="gateway")
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args()
    _run_gateway(args.env_file)


def run_connector_cli() -> None:
    parser = argparse.ArgumentParser(prog="connector", allow_abbrev=False)
    parser.add_argument("--env-file", type=Path, default=None)
    args, rest = parser.parse_known_args()
    _run_connector(args.env_file, rest)


def main() -> None:
    main_app()
