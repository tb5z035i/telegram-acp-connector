from __future__ import annotations

import asyncio
import json
import logging
from asyncio.subprocess import Process
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram_acp_connector.domain import AgentInfo

LOGGER = logging.getLogger(__name__)


UpdateCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AcpClient:
    def __init__(self, command: list[str], workdir: Path) -> None:
        if not command:
            raise ValueError("ACP command must not be empty")
        self.command = command
        self.workdir = workdir
        self.process: Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._update_callback: UpdateCallback | None = None
        self._initialized = False
        self._prompt_lock = asyncio.Lock()
        self.session_id: str | None = None
        self.agent_info = AgentInfo()
        self.agent_capabilities: dict[str, Any] = {}

    async def ensure_started(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self.process.stdout is None or self.process.stdin is None or self.process.stderr is None:
            raise RuntimeError("Failed to create ACP subprocess pipes")
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._initialized = False
        self.session_id = None

    async def initialize(self) -> None:
        await self.ensure_started()
        if self._initialized:
            return
        response = await self._request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {
                    "name": "telegram-acp-connector",
                    "title": "Telegram ACP Connector",
                    "version": "0.1.0",
                },
            },
        )
        self.agent_info = AgentInfo.model_validate(response.get("agentInfo") or {})
        self.agent_capabilities = response.get("agentCapabilities") or {}
        self._initialized = True

    async def ensure_session(self) -> str:
        await self.initialize()
        if self.session_id is not None:
            return self.session_id
        response = await self._request(
            "session/new",
            {
                "cwd": str(self.workdir),
                "mcpServers": [],
            },
        )
        self.session_id = str(response["sessionId"])
        return self.session_id

    async def prompt(self, text: str, on_update: UpdateCallback | None = None) -> str:
        async with self._prompt_lock:
            session_id = await self.ensure_session()
            self._update_callback = on_update
            try:
                await self._request(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": text}],
                    },
                )
            finally:
                self._update_callback = None
            return session_id

    async def reset(self) -> None:
        await self.close()
        await self.ensure_started()
        await self.initialize()

    async def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self.process = None
        self._stdout_task = None
        self._stderr_task = None
        self._initialized = False
        self.session_id = None

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("ACP subprocess is not running")
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.process.stdin.drain()
        return await future

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                LOGGER.debug("Ignoring non-JSON ACP stdout line: %r", line)
                continue

            if "id" in message and "method" not in message:
                request_id = int(message["id"])
                future = self._pending.pop(request_id, None)
                if future is None:
                    continue
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])))
                else:
                    future.set_result(message.get("result") or {})
                continue

            method = message.get("method")
            if method == "session/update" and self._update_callback is not None:
                await self._update_callback(message.get("params") or {})

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            LOGGER.debug("ACP stderr: %s", line.decode("utf-8").rstrip())
