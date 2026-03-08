from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from telegram_acp_connector.config import ConnectorSettings
from telegram_acp_connector.connector.runtime import ConnectorRuntime


@pytest.mark.asyncio
async def test_connector_registers_and_streams_prompt_updates(tmp_path: Path) -> None:
    fake_acp = Path(__file__).parent / "fixtures" / "fake_acp_server.py"
    received: dict[str, object] = {}
    updates: list[str] = []

    async def handler(websocket) -> None:
        register = json.loads(await websocket.recv())
        received["register"] = register
        await websocket.send(
            json.dumps(
                {
                    "type": "registered",
                    "payload": {
                        "agent_id": register["payload"]["agent_id"],
                        "thread_id": 2001,
                        "chat_id": 1001,
                    },
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "prompt",
                    "payload": {
                        "prompt_id": "prompt-1",
                        "agent_id": register["payload"]["agent_id"],
                        "text": "hello",
                    },
                }
            )
        )
        while True:
            message = json.loads(await websocket.recv())
            if message["type"] == "prompt_update":
                updates.append(message["payload"]["text"])
            if message["type"] == "prompt_completed":
                received["completed"] = message
                await websocket.close()
                break

    async with serve(handler, "127.0.0.1", 8765):
        settings = ConnectorSettings(
            connector_gateway_url="ws://127.0.0.1:8765/ws/connectors",
            connector_gateway_shared_token="secret",
            connector_workdir=tmp_path,
            connector_state_dir=tmp_path / ".state",
            connector_default_acp_command=None,
            connector_heartbeat_seconds=60,
            log_level="INFO",
        )
        runtime = ConnectorRuntime(
            settings=settings,
            acp_command=[sys.executable, str(fake_acp)],
        )
        try:
            await runtime._run_once()
        finally:
            await runtime.acp_client.close()

    registration = received["register"]["payload"]
    assert registration["agent_info"]["name"] == "fake-acp"
    assert registration["workdir"] == str(tmp_path)
    assert "".join(updates) == "Echo: hello"
    assert received["completed"]["payload"]["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_connector_reset_restarts_acp_session_counter(tmp_path: Path) -> None:
    fake_acp = Path(__file__).parent / "fixtures" / "fake_acp_server.py"
    settings = ConnectorSettings(
        connector_gateway_url="ws://127.0.0.1:9999/ws/connectors",
        connector_gateway_shared_token="secret",
        connector_workdir=tmp_path,
        connector_state_dir=tmp_path / ".state",
        connector_default_acp_command=None,
        connector_heartbeat_seconds=60,
        log_level="INFO",
    )
    runtime = ConnectorRuntime(
        settings=settings,
        acp_command=[sys.executable, str(fake_acp)],
    )

    await runtime.acp_client.initialize()
    first_session = await runtime.acp_client.prompt("one", on_update=lambda _: _noop())
    await runtime.acp_client.reset()
    second_session = await runtime.acp_client.prompt("two", on_update=lambda _: _noop())
    await runtime.acp_client.close()

    assert first_session == "session-1"
    assert second_session == "session-1"


async def _noop() -> None:
    return None
