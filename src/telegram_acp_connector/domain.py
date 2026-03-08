from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class ConversationState(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    ERRORED = "errored"


class AgentInfo(BaseModel):
    name: str | None = None
    title: str | None = None
    version: str | None = None


class AgentRegistration(BaseModel):
    agent_id: str
    host_id: str
    host_name: str
    workdir: str
    alias: str | None = None
    display_name: str
    launch_command: list[str]
    agent_info: AgentInfo = Field(default_factory=AgentInfo)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class RegisteredAgent(BaseModel):
    agent_id: str
    host_id: str
    host_name: str
    workdir: str
    alias: str | None = None
    display_name: str
    launch_command: list[str]
    agent_info: AgentInfo = Field(default_factory=AgentInfo)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    status: AgentStatus = AgentStatus.ONLINE
    last_seen_at: float


class ThreadBinding(BaseModel):
    agent_id: str
    chat_id: int
    message_thread_id: int
    title: str


class Conversation(BaseModel):
    agent_id: str
    session_id: str | None = None
    state: ConversationState = ConversationState.IDLE
    updated_at: float


class RenderState(BaseModel):
    agent_id: str
    telegram_message_id: int | None = None
    content: str = ""


class PromptRequest(BaseModel):
    prompt_id: str
    agent_id: str
    text: str
    thread_id: int | None = None


class ResetRequest(BaseModel):
    request_id: str
    agent_id: str


class RegistrationAck(BaseModel):
    agent_id: str
    thread_id: int | None = None
    chat_id: int | None = None


class ConnectorEnvelope(BaseModel):
    type: str
    payload: dict[str, Any]


class PromptUpdate(BaseModel):
    prompt_id: str
    agent_id: str
    session_id: str | None = None
    text: str = ""


class PromptCompleted(BaseModel):
    prompt_id: str
    agent_id: str
    session_id: str


class PromptError(BaseModel):
    prompt_id: str
    agent_id: str
    message: str
