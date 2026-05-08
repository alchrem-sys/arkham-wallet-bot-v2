import asyncio
import logging
import re

from aiohttp import web
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import formatter
import analyzer

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class _ThreadFilter(filters.MessageFilter):
    def filter(self, message) -> bool:
        if config.BOT_THREAD_ID is None:
            return True
        return getattr(message, "message_thread_id", None) == config.BOT_THREAD_ID


thread_filter = _ThreadFilter()


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🔍 <b>Wallet Analyzer</b>\n\n"
        "Send any EVM wallet address and I'll show you "
        "suspicious transfers from the last 7 days:\n\n"
        "  🆕 Transfers to/from fresh wallets\n"
        "  🏦 Deposits to exchanges\n"
        "  🔐 Multisig → fresh wallet\n"
        "  🌀 Mixer interactions\n"
        "  🌉 Bridge usage\n"
        "  💰 Large transfers ($50K+)\n\n"
        "Just paste a <code>0x...</code> address to start."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def handle_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip().split()[0]
    if not WALLET_RE.match(text):
        return

    address = text.lower()
    msg = await update.message.reply_text("🔍 Analyzing wallet transfers (last 7 days)...")

    try:
        result = await analyzer.analyze_wallet(address, days=7)
    except Exception as e:
        logger.error(f"analyze error: {e}", exc_info=True)
        await msg.edit_text("❌ Error analyzing wallet. Try again later.")
        return

    if result is None:
        await msg.edit_text("❌ Could not fetch wallet data. Check the address.")
        return

    messages = formatter.format_analysis(result)
    if not messages:
        await msg.edit_text("❌ No data.")
        return

    await msg.edit_text(messages[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    for extra in messages[1:]:
        kwargs = dict(
            chat_id=update.effective_chat.id,
            text=extra,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        if config.BOT_THREAD_ID:
            kwargs["message_thread_id"] = config.BOT_THREAD_ID
        await ctx.bot.send_message(**kwargs)
        await asyncio.sleep(0.3)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "How to use"),
        BotCommand("help", "How to use"),
    ])

    aio_app = web.Application()
    aio_app.router.add_get("/health", lambda _: web.Response(text="OK"))
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logger.info(f"Health check on port {config.PORT}")


def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    tf = thread_filter
    app.add_handler(CommandHandler("start", cmd_start, filters=tf))
    app.add_handler(CommandHandler("help", cmd_start, filters=tf))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"0x[a-fA-F0-9]{40}") & tf,
        handle_address,
    ))

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
