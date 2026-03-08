from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from fastapi import WebSocket

from telegram_acp_connector.connector.runtime import build_prompt_id
from telegram_acp_connector.domain import (
    AgentRegistration,
    AgentStatus,
    ConnectorEnvelope,
    Conversation,
    ConversationState,
    PromptCompleted,
    PromptError,
    PromptRequest,
    PromptUpdate,
    RegisteredAgent,
    RegistrationAck,
    RenderState,
)
from telegram_acp_connector.gateway.storage import GatewayStorage

LOGGER = logging.getLogger(__name__)


class TelegramGateway(Protocol):
    async def ensure_thread_for_agent(self, agent: RegisteredAgent) -> Any: ...

    async def send_thread_message(self, agent_id: str, text: str) -> int | None: ...

    async def edit_thread_message(self, agent_id: str, message_id: int, text: str) -> None: ...

    async def backfill_threads(self) -> None: ...


class ConnectorConnection:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.agent_id: str | None = None
        self._send_lock = asyncio.Lock()

    async def send_envelope(self, envelope: ConnectorEnvelope) -> None:
        async with self._send_lock:
            await self.websocket.send_json(envelope.model_dump(mode="json"))


class GatewayService:
    def __init__(self, storage: GatewayStorage) -> None:
        self.storage = storage
        self.telegram: TelegramGateway | None = None
        self.connections: dict[str, ConnectorConnection] = {}

    def attach_telegram(self, telegram: TelegramGateway) -> None:
        self.telegram = telegram

    async def register_connector(
        self, connection: ConnectorConnection, registration: AgentRegistration
    ) -> RegistrationAck:
        now = time.time()
        await self.storage.touch_host(registration.host_id, registration.host_name)
        agent = RegisteredAgent(
            agent_id=registration.agent_id,
            host_id=registration.host_id,
            host_name=registration.host_name,
            workdir=registration.workdir,
            alias=registration.alias,
            display_name=registration.display_name,
            launch_command=registration.launch_command,
            agent_info=registration.agent_info,
            capabilities=registration.capabilities,
            status=AgentStatus.ONLINE,
            last_seen_at=now,
        )
        await self.storage.upsert_agent(agent)
        self.connections[agent.agent_id] = connection
        connection.agent_id = agent.agent_id

        binding = None
        if self.telegram is not None:
            binding = await self.telegram.ensure_thread_for_agent(agent)
        return RegistrationAck(
            agent_id=agent.agent_id,
            thread_id=None if binding is None else binding.message_thread_id,
            chat_id=None if binding is None else binding.chat_id,
        )

    async def mark_heartbeat(self, agent_id: str) -> None:
        agent = await self.storage.get_agent(agent_id)
        if agent is None:
            return
        agent.last_seen_at = time.time()
        agent.status = AgentStatus.ONLINE
        await self.storage.touch_host(agent.host_id, agent.host_name)
        await self.storage.upsert_agent(agent)

    async def disconnect(self, agent_id: str | None) -> None:
        if agent_id is None:
            return
        self.connections.pop(agent_id, None)
        await self.storage.set_agent_status(agent_id, AgentStatus.OFFLINE)

    async def dispatch_prompt_from_telegram(self, agent_id: str, text: str) -> str:
        connection = self.connections.get(agent_id)
        if connection is None:
            raise RuntimeError("The connector for this agent is offline.")
        await self.storage.clear_render_state(agent_id)
        prompt_id = build_prompt_id()
        await connection.send_envelope(
            ConnectorEnvelope(
                type="prompt",
                payload=PromptRequest(
                    prompt_id=prompt_id,
                    agent_id=agent_id,
                    text=text,
                ).model_dump(mode="json"),
            )
        )
        await self.storage.upsert_conversation(
            Conversation(
                agent_id=agent_id,
                session_id=(
                    await self.storage.get_conversation(agent_id)
                    or Conversation(
                        agent_id=agent_id,
                        updated_at=time.time(),
                    )
                ).session_id,
                state=ConversationState.ACTIVE,
                updated_at=time.time(),
            )
        )
        return prompt_id

    async def dispatch_reset_from_telegram(self, agent_id: str) -> None:
        connection = self.connections.get(agent_id)
        if connection is None:
            raise RuntimeError("The connector for this agent is offline.")
        await connection.send_envelope(
            ConnectorEnvelope(
                type="reset",
                payload={
                    "agent_id": agent_id,
                    "request_id": build_prompt_id(),
                },
            )
        )
        await self.storage.upsert_conversation(
            Conversation(
                agent_id=agent_id,
                session_id=None,
                state=ConversationState.IDLE,
                updated_at=time.time(),
            )
        )
        await self.storage.clear_render_state(agent_id)

    async def render_prompt_update(self, update: PromptUpdate) -> None:
        existing = await self.storage.get_render_state(update.agent_id)
        next_content = (existing.content if existing is not None else "") + update.text
        if self.telegram is None:
            return
        if existing is None or existing.telegram_message_id is None:
            message_id = await self.telegram.send_thread_message(update.agent_id, next_content)
        else:
            await self.telegram.edit_thread_message(
                update.agent_id,
                existing.telegram_message_id,
                next_content,
            )
            message_id = existing.telegram_message_id
        await self.storage.upsert_render_state(
            RenderState(
                agent_id=update.agent_id,
                telegram_message_id=message_id,
                content=next_content,
            )
        )
        await self.storage.upsert_conversation(
            Conversation(
                agent_id=update.agent_id,
                session_id=update.session_id,
                state=ConversationState.ACTIVE,
                updated_at=time.time(),
            )
        )

    async def finish_prompt(self, completed: PromptCompleted) -> None:
        await self.storage.upsert_conversation(
            Conversation(
                agent_id=completed.agent_id,
                session_id=completed.session_id,
                state=ConversationState.IDLE,
                updated_at=time.time(),
            )
        )
        await self.storage.clear_render_state(completed.agent_id)

    async def fail_prompt(self, error: PromptError) -> None:
        await self.storage.upsert_conversation(
            Conversation(
                agent_id=error.agent_id,
                session_id=None,
                state=ConversationState.ERRORED,
                updated_at=time.time(),
            )
        )
        if self.telegram is not None:
            await self.telegram.send_thread_message(error.agent_id, f"❌ {error.message}")
        await self.storage.clear_render_state(error.agent_id)

    async def status_text(self, agent_id: str) -> str:
        agent = await self.storage.get_agent(agent_id)
        if agent is None:
            return "Unknown agent."
        conversation = await self.storage.get_conversation(agent_id)
        session_id = None if conversation is None else conversation.session_id
        state = ConversationState.IDLE if conversation is None else conversation.state
        return (
            f"Agent: {agent.display_name}\n"
            f"Host: {agent.host_name}\n"
            f"Workdir: {agent.workdir}\n"
            f"Status: {agent.status.value}\n"
            f"Conversation state: {state.value}\n"
            f"Session: {session_id or 'none'}"
        )

    async def backfill_threads(self) -> None:
        if self.telegram is not None:
            await self.telegram.backfill_threads()

    async def handle_connector_envelope(
        self, connection: ConnectorConnection, envelope: ConnectorEnvelope
    ) -> ConnectorEnvelope | None:
        if envelope.type == "register":
            ack = await self.register_connector(
                connection,
                AgentRegistration.model_validate(envelope.payload),
            )
            return ConnectorEnvelope(type="registered", payload=ack.model_dump(mode="json"))
        if envelope.type == "heartbeat":
            await self.mark_heartbeat(str(envelope.payload["agent_id"]))
            return None
        if envelope.type == "prompt_update":
            await self.render_prompt_update(PromptUpdate.model_validate(envelope.payload))
            return None
        if envelope.type == "prompt_completed":
            await self.finish_prompt(PromptCompleted.model_validate(envelope.payload))
            return None
        if envelope.type == "prompt_error":
            await self.fail_prompt(PromptError.model_validate(envelope.payload))
            return None
        if envelope.type == "reset_ack":
            return None
        LOGGER.warning("Unhandled connector envelope type: %s", envelope.type)
        return None
