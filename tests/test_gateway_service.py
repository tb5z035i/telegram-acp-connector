from __future__ import annotations

import time
from pathlib import Path

import pytest

from telegram_acp_connector.domain import (
    AgentInfo,
    AgentRegistration,
    PromptCompleted,
    PromptUpdate,
    RegisteredAgent,
    ThreadBinding,
)
from telegram_acp_connector.gateway.service import ConnectorConnection, GatewayService
from telegram_acp_connector.gateway.storage import GatewayStorage


class FakeWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


class FakeTelegram:
    def __init__(self, storage: GatewayStorage) -> None:
        self.storage = storage
        self.created_threads = 0
        self.sent_messages: list[tuple[str, str]] = []
        self.edited_messages: list[tuple[str, int, str]] = []

    async def ensure_thread_for_agent(self, agent: RegisteredAgent) -> ThreadBinding:
        existing = await self.storage.get_thread_binding_for_agent(agent.agent_id)
        if existing is not None:
            return existing
        self.created_threads += 1
        binding = ThreadBinding(
            agent_id=agent.agent_id,
            chat_id=1000,
            message_thread_id=2000 + self.created_threads,
            title=agent.display_name,
        )
        await self.storage.upsert_thread_binding(binding)
        return binding

    async def send_thread_message(self, agent_id: str, text: str) -> int:
        self.sent_messages.append((agent_id, text))
        return len(self.sent_messages)

    async def edit_thread_message(self, agent_id: str, message_id: int, text: str) -> None:
        self.edited_messages.append((agent_id, message_id, text))

    async def backfill_threads(self) -> None:
        for agent in await self.storage.list_agents_without_threads():
            await self.ensure_thread_for_agent(agent)


@pytest.mark.asyncio
async def test_register_connector_is_idempotent(tmp_path: Path) -> None:
    storage = GatewayStorage(tmp_path / "gateway.db")
    await storage.initialize()
    service = GatewayService(storage)
    telegram = FakeTelegram(storage)
    service.attach_telegram(telegram)

    registration = AgentRegistration(
        agent_id="agent-1",
        host_id="host-1",
        host_name="host-1",
        workdir="/tmp/workdir",
        alias=None,
        display_name="Agent One",
        launch_command=["agent", "acp"],
        agent_info=AgentInfo(name="fake-acp"),
        capabilities={},
    )

    ack1 = await service.register_connector(ConnectorConnection(FakeWebSocket()), registration)
    ack2 = await service.register_connector(ConnectorConnection(FakeWebSocket()), registration)

    assert ack1.thread_id == ack2.thread_id
    assert telegram.created_threads == 1
    agents = await storage.list_agents()
    assert len(agents) == 1


@pytest.mark.asyncio
async def test_prompt_updates_are_rendered_and_conversation_persists(tmp_path: Path) -> None:
    storage = GatewayStorage(tmp_path / "gateway.db")
    await storage.initialize()
    service = GatewayService(storage)
    telegram = FakeTelegram(storage)
    service.attach_telegram(telegram)

    agent = RegisteredAgent(
        agent_id="agent-1",
        host_id="host-1",
        host_name="host-1",
        workdir="/tmp/workdir",
        alias=None,
        display_name="Agent One",
        launch_command=["agent", "acp"],
        agent_info=AgentInfo(name="fake-acp"),
        capabilities={},
        last_seen_at=time.time(),
    )
    await storage.upsert_agent(agent)
    await telegram.ensure_thread_for_agent(agent)

    await service.render_prompt_update(
        PromptUpdate(
            prompt_id="prompt-1",
            agent_id="agent-1",
            session_id="session-1",
            text="Echo: he",
        )
    )
    await service.render_prompt_update(
        PromptUpdate(
            prompt_id="prompt-1",
            agent_id="agent-1",
            session_id="session-1",
            text="llo",
        )
    )
    await service.finish_prompt(
        PromptCompleted(
            prompt_id="prompt-1",
            agent_id="agent-1",
            session_id="session-1",
        )
    )

    assert telegram.sent_messages == [("agent-1", "Echo: he")]
    assert telegram.edited_messages == [("agent-1", 1, "Echo: hello")]
    conversation = await storage.get_conversation("agent-1")
    assert conversation is not None
    assert conversation.session_id == "session-1"
    assert conversation.state.value == "idle"
