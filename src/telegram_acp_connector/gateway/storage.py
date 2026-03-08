from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from telegram_acp_connector.domain import (
    AgentStatus,
    Conversation,
    RegisteredAgent,
    RenderState,
    ThreadBinding,
)


class GatewayStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hosts (
                    host_id TEXT PRIMARY KEY,
                    host_name TEXT NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    host_id TEXT NOT NULL,
                    host_name TEXT NOT NULL,
                    workdir TEXT NOT NULL,
                    alias TEXT,
                    display_name TEXT NOT NULL,
                    launch_command TEXT NOT NULL,
                    agent_info_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS thread_bindings (
                    agent_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    message_thread_id INTEGER NOT NULL,
                    title TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    agent_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS render_states (
                    agent_id TEXT PRIMARY KEY,
                    telegram_message_id INTEGER,
                    content TEXT NOT NULL
                );
                """
            )
            await db.commit()

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return None if row is None else str(row[0])

    async def set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await db.commit()

    async def touch_host(self, host_id: str, host_name: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO hosts(host_id, host_name, last_seen_at) VALUES (?, ?, ?)
                ON CONFLICT(host_id) DO UPDATE
                SET host_name = excluded.host_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (host_id, host_name, now),
            )
            await db.commit()

    async def upsert_agent(self, agent: RegisteredAgent) -> RegisteredAgent:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO agents(
                    agent_id, host_id, host_name, workdir, alias, display_name,
                    launch_command, agent_info_json, capabilities_json, status, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    host_id = excluded.host_id,
                    host_name = excluded.host_name,
                    workdir = excluded.workdir,
                    alias = excluded.alias,
                    display_name = excluded.display_name,
                    launch_command = excluded.launch_command,
                    agent_info_json = excluded.agent_info_json,
                    capabilities_json = excluded.capabilities_json,
                    status = excluded.status,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    agent.agent_id,
                    agent.host_id,
                    agent.host_name,
                    agent.workdir,
                    agent.alias,
                    agent.display_name,
                    json.dumps(agent.launch_command),
                    agent.agent_info.model_dump_json(),
                    json.dumps(agent.capabilities),
                    agent.status.value,
                    agent.last_seen_at,
                ),
            )
            await db.commit()
        return agent

    async def list_agents(self) -> list[RegisteredAgent]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, host_id, host_name, workdir, alias, display_name,
                       launch_command, agent_info_json, capabilities_json, status, last_seen_at
                FROM agents
                ORDER BY display_name ASC
                """
            )
            rows = await cursor.fetchall()
        return [self._row_to_agent(row) for row in rows]

    async def get_agent(self, agent_id: str) -> RegisteredAgent | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, host_id, host_name, workdir, alias, display_name,
                       launch_command, agent_info_json, capabilities_json, status, last_seen_at
                FROM agents
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else self._row_to_agent(row)

    async def set_agent_status(self, agent_id: str, status: AgentStatus) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen_at = ? WHERE agent_id = ?",
                (status.value, time.time(), agent_id),
            )
            await db.commit()

    async def upsert_thread_binding(self, binding: ThreadBinding) -> ThreadBinding:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO thread_bindings(agent_id, chat_id, message_thread_id, title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    message_thread_id = excluded.message_thread_id,
                    title = excluded.title
                """,
                (
                    binding.agent_id,
                    binding.chat_id,
                    binding.message_thread_id,
                    binding.title,
                ),
            )
            await db.commit()
        return binding

    async def get_thread_binding_for_agent(self, agent_id: str) -> ThreadBinding | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, chat_id, message_thread_id, title
                FROM thread_bindings
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
            row = await cursor.fetchone()
        return (
            None
            if row is None
            else ThreadBinding(
                agent_id=row[0],
                chat_id=int(row[1]),
                message_thread_id=int(row[2]),
                title=str(row[3]),
            )
        )

    async def get_thread_binding_by_thread(
        self, chat_id: int, message_thread_id: int
    ) -> ThreadBinding | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, chat_id, message_thread_id, title
                FROM thread_bindings
                WHERE chat_id = ? AND message_thread_id = ?
                """,
                (chat_id, message_thread_id),
            )
            row = await cursor.fetchone()
        return (
            None
            if row is None
            else ThreadBinding(
                agent_id=row[0],
                chat_id=int(row[1]),
                message_thread_id=int(row[2]),
                title=str(row[3]),
            )
        )

    async def upsert_conversation(self, conversation: Conversation) -> Conversation:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO conversations(agent_id, session_id, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation.agent_id,
                    conversation.session_id,
                    conversation.state.value,
                    conversation.updated_at,
                ),
            )
            await db.commit()
        return conversation

    async def get_conversation(self, agent_id: str) -> Conversation | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, session_id, state, updated_at
                FROM conversations
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
            row = await cursor.fetchone()
        return (
            None
            if row is None
            else Conversation(
                agent_id=row[0],
                session_id=row[1],
                state=row[2],
                updated_at=float(row[3]),
            )
        )

    async def upsert_render_state(self, state: RenderState) -> RenderState:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO render_states(agent_id, telegram_message_id, content)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    telegram_message_id = excluded.telegram_message_id,
                    content = excluded.content
                """,
                (state.agent_id, state.telegram_message_id, state.content),
            )
            await db.commit()
        return state

    async def get_render_state(self, agent_id: str) -> RenderState | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT agent_id, telegram_message_id, content
                FROM render_states
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return RenderState(
            agent_id=row[0],
            telegram_message_id=row[1],
            content=row[2],
        )

    async def clear_render_state(self, agent_id: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute("DELETE FROM render_states WHERE agent_id = ?", (agent_id,))
            await db.commit()

    async def list_agents_without_threads(self) -> list[RegisteredAgent]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    a.agent_id,
                    a.host_id,
                    a.host_name,
                    a.workdir,
                    a.alias,
                    a.display_name,
                    a.launch_command,
                    a.agent_info_json,
                    a.capabilities_json,
                    a.status,
                    a.last_seen_at
                FROM agents a
                LEFT JOIN thread_bindings t ON t.agent_id = a.agent_id
                WHERE t.agent_id IS NULL
                ORDER BY a.display_name ASC
                """
            )
            rows = await cursor.fetchall()
        return [self._row_to_agent(row) for row in rows]

    def _row_to_agent(self, row: tuple[Any, ...]) -> RegisteredAgent:
        return RegisteredAgent.model_validate(
            {
                "agent_id": row[0],
                "host_id": row[1],
                "host_name": row[2],
                "workdir": row[3],
                "alias": row[4],
                "display_name": row[5],
                "launch_command": json.loads(row[6]),
                "agent_info": json.loads(row[7]),
                "capabilities": json.loads(row[8]),
                "status": row[9],
                "last_seen_at": row[10],
            }
        )
