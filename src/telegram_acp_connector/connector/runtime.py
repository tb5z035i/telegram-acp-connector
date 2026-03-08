from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets import connect

from telegram_acp_connector.acp.client import AcpClient
from telegram_acp_connector.config import ConnectorSettings
from telegram_acp_connector.connector.local_identity import LocalIdentityStore, build_agent_id
from telegram_acp_connector.domain import (
    AgentRegistration,
    ConnectorEnvelope,
    PromptCompleted,
    PromptError,
    PromptRequest,
    PromptUpdate,
)

LOGGER = logging.getLogger(__name__)


def parse_acp_command(default_command: str | None, passthrough_args: list[str]) -> list[str]:
    if passthrough_args:
        return passthrough_args
    if default_command:
        return default_command.split()
    raise ValueError(
        "No ACP command configured. Set CONNECTOR_DEFAULT_ACP_COMMAND or pass a command."
    )


class ConnectorRuntime:
    def __init__(self, settings: ConnectorSettings, acp_command: list[str]) -> None:
        self.settings = settings
        self.acp_command = acp_command
        self.host_name = socket.gethostname()
        self.host_id = self.host_name
        self.identity_store = LocalIdentityStore(settings.connector_state_dir)
        self.instance_id = self.identity_store.get_or_create_instance_id()
        self.acp_client = AcpClient(command=acp_command, workdir=settings.connector_workdir)
        self.agent_id: str | None = None
        self._send_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()

    async def run_forever(self) -> None:
        await self.acp_client.initialize()
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Connector loop failed; retrying")
                await asyncio.sleep(2)

    async def _run_once(self) -> None:
        registration = await self._build_registration()
        self.agent_id = registration.agent_id
        ws_url = self._build_ws_url()
        async with connect(ws_url) as websocket:
            await websocket.send(
                ConnectorEnvelope(
                    type="register", payload=registration.model_dump(mode="json")
                ).model_dump_json()
            )

            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            try:
                async for message in websocket:
                    envelope = ConnectorEnvelope.model_validate(json.loads(message))
                    await self._handle_envelope(websocket, envelope)
            finally:
                heartbeat_task.cancel()

    def _build_ws_url(self) -> str:
        parts = urlsplit(self.settings.connector_gateway_url)
        query = urlencode({"token": self.settings.connector_gateway_shared_token})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    async def _build_registration(self) -> AgentRegistration:
        await self.acp_client.initialize()
        agent_info = self.acp_client.agent_info
        agent_id = build_agent_id(
            hostname=self.host_name,
            workdir=self.settings.connector_workdir,
            configured_agent_id=self.settings.connector_agent_id,
            alias=self.settings.connector_alias,
            acp_name=agent_info.name,
            instance_id=self.instance_id,
        )
        display_name = (
            self.settings.connector_alias
            or agent_info.title
            or agent_info.name
            or self.settings.connector_workdir.name
            or agent_id
        )
        return AgentRegistration(
            agent_id=agent_id,
            host_id=self.host_id,
            host_name=self.host_name,
            workdir=str(self.settings.connector_workdir),
            alias=self.settings.connector_alias,
            display_name=display_name,
            launch_command=self.acp_command,
            agent_info=agent_info,
            capabilities=self.acp_client.agent_capabilities,
        )

    async def _heartbeat_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(self.settings.connector_heartbeat_seconds)
            if self.agent_id is None:
                continue
            await self._send(
                websocket,
                ConnectorEnvelope(
                    type="heartbeat",
                    payload={"agent_id": self.agent_id},
                ),
            )

    async def _handle_envelope(self, websocket: Any, envelope: ConnectorEnvelope) -> None:
        if envelope.type == "prompt":
            request = PromptRequest.model_validate(envelope.payload)
            await self._handle_prompt(websocket, request)
            return
        if envelope.type == "reset":
            await self.acp_client.reset()
            await self._send(
                websocket,
                ConnectorEnvelope(
                    type="reset_ack",
                    payload={
                        "agent_id": envelope.payload["agent_id"],
                        "request_id": envelope.payload["request_id"],
                    },
                ),
            )
            return

    async def _handle_prompt(self, websocket: Any, request: PromptRequest) -> None:
        async with self._prompt_lock:
            try:
                session_id = await self.acp_client.prompt(
                    request.text,
                    on_update=lambda params: self._forward_update(websocket, request, params),
                )
                await self._send(
                    websocket,
                    ConnectorEnvelope(
                        type="prompt_completed",
                        payload=PromptCompleted(
                            prompt_id=request.prompt_id,
                            agent_id=request.agent_id,
                            session_id=session_id,
                        ).model_dump(mode="json"),
                    ),
                )
            except Exception as exc:
                await self._send(
                    websocket,
                    ConnectorEnvelope(
                        type="prompt_error",
                        payload=PromptError(
                            prompt_id=request.prompt_id,
                            agent_id=request.agent_id,
                            message=str(exc),
                        ).model_dump(mode="json"),
                    ),
                )

    async def _forward_update(
        self, websocket: Any, request: PromptRequest, params: dict[str, Any]
    ) -> None:
        text = _extract_update_text(params)
        if not text:
            return
        await self._send(
            websocket,
            ConnectorEnvelope(
                type="prompt_update",
                payload=PromptUpdate(
                    prompt_id=request.prompt_id,
                    agent_id=request.agent_id,
                    session_id=params.get("sessionId"),
                    text=text,
                ).model_dump(mode="json"),
            ),
        )

    async def _send(self, websocket: Any, envelope: ConnectorEnvelope) -> None:
        async with self._send_lock:
            await websocket.send(envelope.model_dump_json())


def _extract_update_text(payload: dict[str, Any]) -> str:
    update = payload.get("update")
    if isinstance(update, dict):
        for key in ("text", "delta", "chunk", "content"):
            value = update.get(key)
            if isinstance(value, str):
                return value
        parts = []
        for value in update.values():
            if isinstance(value, str):
                parts.append(value)
        if parts:
            return "\n".join(parts)

    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return "\n".join(part for part in parts if part)[:4000]


def build_prompt_id() -> str:
    return uuid.uuid4().hex
