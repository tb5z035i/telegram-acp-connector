from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from telegram_acp_connector.config import GatewaySettings
from telegram_acp_connector.domain import ConnectorEnvelope
from telegram_acp_connector.gateway.service import ConnectorConnection, GatewayService
from telegram_acp_connector.gateway.storage import GatewayStorage
from telegram_acp_connector.gateway.telegram import TelegramGatewayService


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = GatewayStorage(app.state.settings.gateway_database_path)
    await storage.initialize()
    service = GatewayService(storage)
    telegram_service = TelegramGatewayService(app.state.settings, storage, service)
    service.attach_telegram(telegram_service)

    app.state.storage = storage
    app.state.gateway_service = service
    app.state.telegram_service = telegram_service

    await telegram_service.startup()
    try:
        yield
    finally:
        await telegram_service.shutdown()


def create_app(settings: GatewaySettings) -> FastAPI:
    app = FastAPI(title="telegram-acp-gateway", lifespan=lifespan)
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws/connectors")
    async def connectors_ws(websocket: WebSocket) -> None:
        token = websocket.query_params.get("token")
        if token != settings.gateway_shared_token:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        connection = ConnectorConnection(websocket)
        try:
            while True:
                payload = await websocket.receive_json()
                envelope = ConnectorEnvelope.model_validate(payload)
                response = await app.state.gateway_service.handle_connector_envelope(
                    connection, envelope
                )
                if response is not None:
                    await connection.send_envelope(response)
        except WebSocketDisconnect:
            await app.state.gateway_service.disconnect(connection.agent_id)

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request) -> dict[str, bool]:
        if not settings.telegram_webhook_url:
            raise HTTPException(status_code=404, detail="Webhook mode is not enabled")
        try:
            await app.state.telegram_service.process_webhook_update(
                await request.json(),
                secret_token=request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"ok": True}

    return app
