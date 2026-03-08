from __future__ import annotations

import logging
from typing import Any

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_acp_connector.config import GatewaySettings
from telegram_acp_connector.domain import RegisteredAgent, ThreadBinding
from telegram_acp_connector.gateway.service import GatewayService
from telegram_acp_connector.gateway.storage import GatewayStorage

LOGGER = logging.getLogger(__name__)


class TelegramGatewayService:
    def __init__(
        self,
        settings: GatewaySettings,
        storage: GatewayStorage,
        gateway_service: GatewayService,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.gateway_service = gateway_service
        self.application: Application | None = None
        self.bot: Bot | None = None

    async def startup(self) -> None:
        self.application = ApplicationBuilder().token(self.settings.telegram_bot_token).build()
        self.bot = self.application.bot
        self.application.add_handler(CommandHandler("start", self._start_command))
        self.application.add_handler(CommandHandler("help", self._help_command))
        self.application.add_handler(CommandHandler("status", self._status_command))
        self.application.add_handler(CommandHandler("reset", self._reset_command))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._text_message)
        )
        await self.application.initialize()
        await self.application.start()
        await self._assert_threaded_mode_enabled()
        if self.settings.telegram_webhook_url:
            await self.bot.set_webhook(
                url=self.settings.telegram_webhook_url,
                secret_token=self.settings.telegram_webhook_secret or None,
            )
        else:
            assert self.application.updater is not None
            await self.application.updater.start_polling(drop_pending_updates=False)

    async def shutdown(self) -> None:
        if self.application is None:
            return
        if self.application.updater is not None and self.application.updater.running:
            await self.application.updater.stop()
        if self.settings.telegram_webhook_url and self.bot is not None:
            await self.bot.delete_webhook(drop_pending_updates=False)
        await self.application.stop()
        await self.application.shutdown()

    async def process_webhook_update(
        self, payload: dict[str, Any], secret_token: str | None = None
    ) -> None:
        if (
            self.settings.telegram_webhook_secret
            and secret_token != self.settings.telegram_webhook_secret
        ):
            raise PermissionError("Invalid Telegram webhook secret")
        if self.application is None or self.bot is None:
            raise RuntimeError("Telegram application is not started")
        update = Update.de_json(payload, self.bot)
        await self.application.process_update(update)

    async def ensure_thread_for_agent(self, agent: RegisteredAgent) -> ThreadBinding | None:
        existing = await self.storage.get_thread_binding_for_agent(agent.agent_id)
        if existing is not None:
            return existing
        chat_id = await self._resolve_chat_id()
        if chat_id is None:
            LOGGER.info(
                "Telegram chat id unknown yet; postponing thread creation for %s", agent.agent_id
            )
            return None
        assert self.bot is not None
        topic = await self.bot.create_forum_topic(
            chat_id=chat_id,
            name=_build_thread_title(agent),
        )
        binding = ThreadBinding(
            agent_id=agent.agent_id,
            chat_id=chat_id,
            message_thread_id=topic.message_thread_id,
            title=_build_thread_title(agent),
        )
        await self.storage.upsert_thread_binding(binding)
        return binding

    async def send_thread_message(self, agent_id: str, text: str) -> int | None:
        binding = await self.storage.get_thread_binding_for_agent(agent_id)
        if binding is None:
            return None
        assert self.bot is not None
        message = await self.bot.send_message(
            chat_id=binding.chat_id,
            text=text[:4000],
            message_thread_id=binding.message_thread_id,
        )
        return message.message_id

    async def edit_thread_message(self, agent_id: str, message_id: int, text: str) -> None:
        binding = await self.storage.get_thread_binding_for_agent(agent_id)
        if binding is None:
            return
        assert self.bot is not None
        await self.bot.edit_message_text(
            chat_id=binding.chat_id,
            message_id=message_id,
            text=text[:4000],
        )

    async def backfill_threads(self) -> None:
        agents = await self.storage.list_agents_without_threads()
        for agent in agents:
            await self.ensure_thread_for_agent(agent)

    async def _resolve_chat_id(self) -> int | None:
        if self.settings.telegram_chat_id is not None:
            return self.settings.telegram_chat_id
        stored = await self.storage.get_setting("telegram_chat_id")
        return None if stored is None else int(stored)

    async def _remember_chat_id(self, chat_id: int) -> None:
        await self.storage.set_setting("telegram_chat_id", str(chat_id))

    async def _assert_threaded_mode_enabled(self) -> None:
        assert self.bot is not None
        bot_user = await self.bot.get_me()
        has_topics_enabled = getattr(bot_user, "has_topics_enabled", None)
        if has_topics_enabled is None:
            api_kwargs = getattr(bot_user, "api_kwargs", None)
            if isinstance(api_kwargs, dict):
                has_topics_enabled = api_kwargs.get("has_topics_enabled")
        if not bool(has_topics_enabled):
            raise RuntimeError("Telegram Threaded Mode must be enabled for the bot in @BotFather.")

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        assert update.effective_chat is not None
        await self._remember_chat_id(update.effective_chat.id)
        await self.gateway_service.backfill_threads()
        assert update.effective_message is not None
        await update.effective_message.reply_text(
            "✅ Connector authorized. New ACP agents will appear as Telegram threads."
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        assert update.effective_message is not None
        await update.effective_message.reply_text(
            "/start — authorize this private chat and create missing threads\n"
            "/status — show the bound agent status inside an agent thread\n"
            "/reset — restart the bound ACP server inside an agent thread\n"
            "Any other text inside an agent thread is forwarded to ACP."
        )

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        binding = await self._resolve_binding(update)
        assert update.effective_message is not None
        if binding is None:
            await update.effective_message.reply_text(
                "Send /status from inside an agent thread after /start has authorized the bot."
            )
            return
        await update.effective_message.reply_text(
            await self.gateway_service.status_text(binding.agent_id)
        )

    async def _reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        binding = await self._resolve_binding(update)
        assert update.effective_message is not None
        if binding is None:
            await update.effective_message.reply_text("Send /reset from inside an agent thread.")
            return
        await self.gateway_service.dispatch_reset_from_telegram(binding.agent_id)
        await update.effective_message.reply_text("🔄 Reset requested.")

    async def _text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        binding = await self._resolve_binding(update)
        assert update.effective_message is not None
        if binding is None:
            await update.effective_message.reply_text(
                "Open an agent thread first. Use /start in the root chat if needed."
            )
            return
        text = update.effective_message.text or ""
        try:
            await self.gateway_service.dispatch_prompt_from_telegram(binding.agent_id, text)
        except RuntimeError as exc:
            await update.effective_message.reply_text(f"❌ {exc}")

    async def _resolve_binding(self, update: Update) -> ThreadBinding | None:
        if update.effective_chat is None or update.effective_message is None:
            return None
        thread_id = getattr(update.effective_message, "message_thread_id", None)
        if thread_id is None:
            return None
        return await self.storage.get_thread_binding_by_thread(update.effective_chat.id, thread_id)

    async def _authorize(self, update: Update) -> bool:
        user = update.effective_user
        if user is None or user.id != self.settings.telegram_allowed_user_id:
            if update.effective_message is not None:
                try:
                    await update.effective_message.reply_text("Unauthorized.")
                except TelegramError:
                    LOGGER.warning("Unable to reply to unauthorized user")
            return False
        return True


def _build_thread_title(agent: RegisteredAgent) -> str:
    title = agent.display_name.strip() or agent.agent_id
    return title[:128]
