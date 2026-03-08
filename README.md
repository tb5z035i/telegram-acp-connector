# telegram-acp-connector

A Python implementation of a **Telegram ↔ ACP gateway plus edge connector**.

The system is split into two parts:

- a **gateway** that stays online, owns the Telegram bot integration, and routes prompts
- a **connector** that runs on a host, owns a local ACP subprocess, auto-registers to the gateway, and exposes that ACP agent as a Telegram thread

## Current MVP

This repository now ships an MVP with these behaviors:

- single-user Telegram bot access
- one connector instance owns one ACP subprocess
- one registered connector becomes one Telegram thread/topic
- connector auto-registers to the always-on gateway
- connector can launch ACP from:
  - a configured default command, or
  - a passthrough CLI invocation like `connector agent acp`
- `/reset` inside a thread restarts the connector-owned ACP subprocess
- stable connector identity via:
  1. configured agent id
  2. configured alias
  3. persisted local random instance id

## Architecture

```text
Telegram user
   │
   ▼
Gateway (FastAPI + python-telegram-bot + SQLite)
   │  websocket
   ▼
Connector (Typer CLI)
   │  stdio JSON-RPC
   ▼
ACP subprocess
```

### Gateway responsibilities

- authorize one Telegram user
- maintain host/agent registrations
- create and bind Telegram threads/topics
- route Telegram prompts to the right connector
- stream ACP updates back into Telegram

### Connector responsibilities

- start in a chosen workdir
- launch and own the ACP subprocess
- initialize ACP and create/reuse sessions
- connect outward to the gateway over websocket
- restart ACP on `/reset`

## Requirements

- Python 3.12+
- a Telegram bot token from [@BotFather](https://t.me/BotFather)
- Telegram **Threaded Mode** enabled for that bot in BotFather
- your Telegram numeric user id
- an ACP command to launch locally, for example `agent acp`

## Installation

```bash
python3 -m pip install --user -e '.[dev]'
```

## Configuration

Start from:

```bash
cp .env.example .env
```

Important settings:

### Gateway

- `GATEWAY_HOST`
- `GATEWAY_PORT`
- `GATEWAY_DATABASE_PATH`
- `GATEWAY_SHARED_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_ID`
- `TELEGRAM_CHAT_ID` *(optional; otherwise learned from `/start`)*
- `TELEGRAM_WEBHOOK_URL` *(optional; polling is used if unset)*
- `TELEGRAM_WEBHOOK_SECRET` *(optional)*

### Connector

- `CONNECTOR_GATEWAY_URL`
- `CONNECTOR_GATEWAY_SHARED_TOKEN`
- `CONNECTOR_WORKDIR`
- `CONNECTOR_STATE_DIR`
- `CONNECTOR_ALIAS` *(optional)*
- `CONNECTOR_AGENT_ID` *(optional)*
- `CONNECTOR_DEFAULT_ACP_COMMAND` *(optional if you pass a command on the CLI)*
- `CONNECTOR_HEARTBEAT_SECONDS`

## Running

### 1. Start the gateway

Using the dedicated script:

```bash
gateway --env-file .env
```

or:

```bash
telegram-acp-connector gateway --env-file .env
```

### 2. Authorize the Telegram private chat

Send `/start` to the bot from the allowed Telegram account.

If `TELEGRAM_CHAT_ID` is not already configured, the gateway stores that private chat id and then creates any missing agent threads/topics.

### 3. Start a connector with a configured default ACP command

```bash
connector --env-file .env
```

### 4. Or start a connector with a passthrough ACP command

```bash
connector --env-file .env agent acp
```

This is the convenient mode described in the design: everything after `connector` is treated as the ACP command to launch.

### 5. Multiple agents / multiple workdirs

The MVP model is:

- **one connector instance = one ACP subprocess = one Telegram thread**

If you want another workdir or another agent, start another connector instance.

## Telegram commands

### Root chat

- `/start` — authorize/store the private chat id and backfill missing threads
- `/help` — show help

### Inside an agent thread

- `/status` — show host/workdir/session status for the bound ACP agent
- `/reset` — restart the connector-owned ACP subprocess and clear session state
- any normal text message — forward a prompt to ACP

## MCP notes

The current ACP-side canonical pattern is still that clients may provide `mcpServers` during `session/new`.

This MVP keeps MCP simple:

- no gateway-level MCP orchestration
- no dedicated MCP registry layer
- MCP can be added later either by:
  - letting the ACP server manage MCP itself, or
  - extending the connector to pass configured `mcpServers`

## Development

Run tests:

```bash
python3 -m pytest -q
```

Run lint:

```bash
python3 -m ruff check src tests
```

## Reference

For Telegram threaded-mode behavior, this implementation was informed by:

- https://github.com/tb5z035i/cursor-tg
