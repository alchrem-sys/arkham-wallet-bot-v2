import asyncio
import logging
import re
import time

from aiohttp import web
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import formatter
import arkham_api
import storage
from poll_manager import TransferPollManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
WALLET_LINE_RE = re.compile(r"^(0x[a-fA-F0-9]{40})(?:\s+(.+?))?$")
NUMBER_RE = re.compile(r"^[\d_]+(?:\.\d+)?$")

DUST_USD_THRESHOLD = 0.10
BATCH_WINDOW_SECONDS = 1

_app: Application | None = None
_poll_manager: TransferPollManager | None = None
_pending_batches: dict[tuple[int, str], dict] = {}

# Cooldown for wallet total refresh (Arkham API calls)
_last_refresh: dict[str, float] = {}
REFRESH_COOLDOWN = 300


class _ThreadFilter(filters.MessageFilter):
    def filter(self, message) -> bool:
        if config.BOT_THREAD_ID is None:
            return True
        return getattr(message, "message_thread_id", None) == config.BOT_THREAD_ID


thread_filter = _ThreadFilter()


def parse_wallet_message(text: str) -> tuple[str, str | None, float | None] | None:
    text = text.strip()
    m = WALLET_LINE_RE.match(text)
    if not m:
        return None
    address = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    if not rest:
        return address, None, None
    parts = rest.split()
    threshold = None
    if NUMBER_RE.match(parts[-1]):
        try:
            threshold = float(parts[-1].replace("_", ""))
            parts = parts[:-1]
        except ValueError:
            pass
    name = " ".join(parts).strip() or None
    return address, name, threshold


# ─── Transfer handler (from poll_manager) ─────────────────────────────────────

async def on_transfer(event: dict, watched_address: str) -> None:
    if not _app:
        return

    tx_hash = event.get("hash", "")
    if not tx_hash:
        return

    value_usd = float(event.get("value_usd") or 0)
    token_symbol = event.get("token_symbol", "")
    amount = event.get("amount", 0)
    if isinstance(amount, (int, float)):
        event["amount"] = formatter.fmt_amount(amount) if amount else "0"

    addr = watched_address.lower()
    subscribers = await storage.get_subscribers(addr)
    if not subscribers:
        return

    dedup_key = f"tx-addr:{tx_hash}:{addr}"
    if await storage.is_event_seen(dedup_key):
        return

    logger.info(f"NOTIFY {tx_hash[:14]}/{addr[:10]}: {token_symbol} ${value_usd:.2f}")

    for chat_id in subscribers:
        threshold = await storage.get_wallet_threshold(chat_id, addr)
        if threshold is not None and value_usd < threshold:
            continue
        await enqueue_batch(chat_id, addr, [event])

    asyncio.create_task(_maybe_refresh_total(addr))


# ─── Batch & send ──────────────────────────────────────────────────────────────

async def enqueue_batch(chat_id: int, address: str, events: list[dict]) -> None:
    key = (chat_id, address)
    if key not in _pending_batches:
        _pending_batches[key] = {"events": [], "task": None}
    batch = _pending_batches[key]
    batch["events"].extend(events)

    if batch["task"] and not batch["task"].done():
        batch["task"].cancel()
    batch["task"] = asyncio.create_task(_flush_batch(key))


async def _flush_batch(key: tuple[int, str]) -> None:
    try:
        await asyncio.sleep(BATCH_WINDOW_SECONDS)
    except asyncio.CancelledError:
        return

    batch = _pending_batches.pop(key, None)
    if not batch or not batch["events"]:
        return

    chat_id, address = key
    events = batch["events"]

    name = await storage.get_wallet_name(chat_id, address)
    total = await storage.get_wallet_total_usd(address)

    # Build label map from Arkham entity data embedded in events
    label_map: dict[str, str] = {}
    for ev in events:
        if ev.get("from_label") and ev["from"]:
            label_map[ev["from"]] = ev["from_label"]
        if ev.get("to_label") and ev["to"]:
            label_map[ev["to"]] = ev["to_label"]

    msg = formatter.format_batch(events, address, name, total, label_map)
    try:
        kwargs = dict(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        if config.BOT_THREAD_ID:
            kwargs["message_thread_id"] = config.BOT_THREAD_ID
        await _app.bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"send_message: {e}")


async def _maybe_refresh_total(address: str) -> None:
    now = time.time()
    if now - _last_refresh.get(address, 0) < REFRESH_COOLDOWN:
        return
    _last_refresh[address] = now
    try:
        portfolio = await arkham_api.get_portfolio(address)
        if portfolio:
            total = _calc_portfolio_total(portfolio)
            await storage.set_wallet_total_usd(address, total)
    except Exception as e:
        logger.warning(f"refresh_total({address}): {e}")


def _calc_portfolio_total(portfolio: dict) -> float:
    total = 0.0
    if isinstance(portfolio, dict):
        for chain_data in portfolio.values():
            if isinstance(chain_data, dict):
                for token_data in chain_data.values():
                    if isinstance(token_data, dict):
                        total += float(token_data.get("usd", 0) or 0)
    return total


def _calc_chain_breakdown(portfolio: dict) -> dict[str, float]:
    breakdown: dict[str, float] = {}
    if isinstance(portfolio, dict):
        for chain_name, chain_data in portfolio.items():
            if isinstance(chain_data, dict):
                chain_total = 0.0
                for token_data in chain_data.values():
                    if isinstance(token_data, dict):
                        chain_total += float(token_data.get("usd", 0) or 0)
                if chain_total > 0:
                    chain_id = config.ARKHAM_CHAIN_MAP.get(chain_name, chain_name)
                    breakdown[chain_id] = chain_total
    return breakdown


# ─── Inline menu ───────────────────────────────────────────────────────────────

MENU_PAGE_SIZE = 5


async def _menu_text_and_kb(chat_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    wallets = sorted(await storage.get_wallets(chat_id))
    total = len(wallets)
    total_pages = max(1, (total + MENU_PAGE_SIZE - 1) // MENU_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    buttons = []
    for addr in wallets[page * MENU_PAGE_SIZE:(page + 1) * MENU_PAGE_SIZE]:
        name = await storage.get_wallet_name(chat_id, addr)
        usd = await storage.get_wallet_total_usd(addr)
        label = name or formatter.short_addr(addr)
        if usd > 0:
            label += f" · {formatter.fmt_usd(usd)}"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"wd:{addr}"),
            InlineKeyboardButton("🗑", callback_data=f"rmc:{addr}:{page}"),
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"mn:{page - 1}"))
    nav.append(InlineKeyboardButton("🔄", callback_data=f"mn:{page}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"mn:{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("➕ Add wallet", callback_data="addh")])

    header = f"📋 <b>Wallets ({total}/{config.MAX_WALLETS_PER_CHAT})</b>"
    if total_pages > 1:
        header += f" · page {page + 1}/{total_pages}"
    if total == 0:
        header += "\n\nNo wallets yet. Send an address to start."

    return header, InlineKeyboardMarkup(buttons)


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text, kb = await _menu_text_and_kb(chat_id, 0)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id = update.effective_chat.id

    if data.startswith("mn:"):
        page = int(data.split(":")[1])
        text, kb = await _menu_text_and_kb(chat_id, page)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif data.startswith("wd:"):
        addr = data[3:]
        await _show_wallet_detail(query, chat_id, addr)

    elif data.startswith("rmc:"):
        _, addr, page = data.split(":", 2)
        name = await storage.get_wallet_name(chat_id, addr)
        title = name or formatter.short_addr(addr)
        await query.edit_message_text(
            f"Remove <b>{title}</b>?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Remove", callback_data=f"rm:{addr}:{page}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"mn:{page}"),
                ]
            ]),
        )

    elif data.startswith("rm:"):
        _, addr, page = data.split(":", 2)
        no_more_subs = await storage.remove_wallet(chat_id, addr)
        if no_more_subs and _poll_manager:
            await _poll_manager.remove_address(addr)
        text, kb = await _menu_text_and_kb(chat_id, int(page))
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif data == "addh":
        await query.edit_message_text(
            "Send a wallet address to start tracking:\n\n"
            "<code>0x123...</code>\n"
            "<code>0x123... name</code>\n"
            "<code>0x123... name 1000</code>\n\n"
            "Last number = min USD threshold.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back", callback_data="mn:0"),
            ]]),
        )


async def _show_wallet_detail(query, chat_id: int, addr: str) -> None:
    name = await storage.get_wallet_name(chat_id, addr)
    usd = await storage.get_wallet_total_usd(addr)
    threshold = await storage.get_wallet_threshold(chat_id, addr)

    title = name or formatter.short_addr(addr)
    lines = [f"<b>{title}</b>", f"<code>{addr}</code>", ""]
    if usd > 0:
        lines.append(f"💰 {formatter.fmt_usd(usd)}")
    lines.append(f"📏 {'No min' if threshold is None else formatter.fmt_usd(threshold)}")
    lines += [
        "",
        f"Rename: <code>/rename {addr} name</code>",
        f"Set min: <code>/threshold {addr} 500</code>",
    ]

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Remove", callback_data=f"rmc:{addr}:0")],
            [InlineKeyboardButton("← Back", callback_data="mn:0")],
        ]),
    )


# ─── Telegram command handlers ─────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 <b>Wallet Monitor Bot</b>\n\n"
        "Send a wallet address to start tracking:\n\n"
        "<code>0x123...</code>\n"
        "<code>0x123... whale</code>\n"
        "<code>0x123... amora 1000</code>\n\n"
        "<b>Commands:</b>\n"
        "/menu — wallet manager\n"
        "/list — tracked wallets\n"
        "/stop <code>0x...</code> — stop tracking\n"
        "/rename <code>0x... name</code> — rename\n"
        "/threshold <code>0x... 500</code> — change min USD"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    wallets = await storage.get_wallets(chat_id)
    if not wallets:
        await update.message.reply_text("No wallets tracked yet. Send an address to start!")
        return

    lines = [f"📋 <b>Tracked ({len(wallets)}/{config.MAX_WALLETS_PER_CHAT}):</b>\n"]
    for i, addr in enumerate(sorted(wallets), 1):
        name = await storage.get_wallet_name(chat_id, addr)
        threshold = await storage.get_wallet_threshold(chat_id, addr)
        total = await storage.get_wallet_total_usd(addr)
        title = name or formatter.short_addr(addr)
        line = f"{i}. <b>{title}</b>"
        if name:
            line += f" — <code>{formatter.short_addr(addr)}</code>"
        details = []
        if total > 0:
            details.append(formatter.fmt_usd(total))
        if threshold is not None:
            details.append(f"min {formatter.fmt_usd(threshold)}")
        else:
            details.append("no min")
        if details:
            line += f"  ({', '.join(details)})"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("Usage: /stop <code>0x...</code>", parse_mode=ParseMode.HTML)
        return
    address = ctx.args[0].lower()
    if not WALLET_RE.match(address):
        await update.message.reply_text("Invalid address.")
        return
    wallets = await storage.get_wallets(chat_id)
    if address not in wallets:
        await update.message.reply_text(f"Not tracking <code>{address}</code>", parse_mode=ParseMode.HTML)
        return

    no_more_subs = await storage.remove_wallet(chat_id, address)
    if no_more_subs and _poll_manager:
        await _poll_manager.remove_address(address)
    await update.message.reply_text(
        f"✅ Stopped tracking <code>{formatter.short_addr(address)}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_rename(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /rename <code>0x... name</code>", parse_mode=ParseMode.HTML)
        return
    address = ctx.args[0].lower()
    if not WALLET_RE.match(address):
        await update.message.reply_text("Invalid address.")
        return
    name = " ".join(ctx.args[1:]).strip()
    wallets = await storage.get_wallets(chat_id)
    if address not in wallets:
        await update.message.reply_text(f"Not tracking <code>{address}</code>", parse_mode=ParseMode.HTML)
        return
    await storage.set_wallet_name(chat_id, address, name)
    await update.message.reply_text(f"✅ Renamed to <b>{name}</b>", parse_mode=ParseMode.HTML)


async def cmd_threshold(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /threshold <code>0x... amount</code>\n"
            "Use <code>0</code> to remove threshold (show all)",
            parse_mode=ParseMode.HTML,
        )
        return
    address = ctx.args[0].lower()
    if not WALLET_RE.match(address):
        await update.message.reply_text("Invalid address.")
        return
    try:
        amount = float(ctx.args[1])
        if amount < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Invalid amount.")
        return

    wallets = await storage.get_wallets(chat_id)
    if address not in wallets:
        await update.message.reply_text(f"Not tracking <code>{address}</code>", parse_mode=ParseMode.HTML)
        return

    if amount == 0:
        from upstash_redis import Redis
        def _del():
            Redis(url=config.REDIS_URL, token=config.REDIS_TOKEN).delete(
                f"bot:chat:{chat_id}:wallet:{address}:threshold"
            )
        await asyncio.to_thread(_del)
        await update.message.reply_text("✅ Threshold removed (showing all transfers).")
    else:
        await storage.set_wallet_threshold(chat_id, address, amount)
        await update.message.reply_text(
            f"✅ Threshold set to <b>{formatter.fmt_usd(amount)}</b>",
            parse_mode=ParseMode.HTML,
        )


async def handle_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parsed = parse_wallet_message(update.message.text)
    if not parsed:
        return
    address, name, threshold = parsed

    count = await storage.count_wallets(chat_id)
    wallets = await storage.get_wallets(chat_id)
    is_update = address in wallets
    if not is_update and count >= config.MAX_WALLETS_PER_CHAT:
        await update.message.reply_text(
            f"Max {config.MAX_WALLETS_PER_CHAT} wallets. Use /stop to remove one."
        )
        return

    msg = await update.message.reply_text("🔍 Looking up wallet...")

    portfolio, intel = await asyncio.gather(
        arkham_api.get_portfolio(address),
        arkham_api.get_intelligence(address),
    )

    if portfolio is None:
        await msg.edit_text("❌ Could not fetch wallet data.")
        return

    total = _calc_portfolio_total(portfolio)
    chain_breakdown = _calc_chain_breakdown(portfolio)
    entity_name = arkham_api.extract_entity(intel, address)
    if not name and entity_name:
        name = entity_name

    await storage.set_wallet_total_usd(address, total)
    await storage.add_wallet(chat_id, address, name=name, threshold=threshold)

    if _poll_manager and not is_update:
        await _poll_manager.add_address(address)

    portfolio_text = formatter.format_portfolio(
        address, total, name=name, threshold=threshold,
        entity=entity_name, chain_breakdown=chain_breakdown,
    )
    await msg.edit_text(portfolio_text, parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True)


# ─── App startup ───────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    global _app, _poll_manager
    _app = app

    await app.bot.set_my_commands([
        BotCommand("menu", "Wallet manager (pin this)"),
        BotCommand("list", "Show tracked wallets"),
        BotCommand("stop", "Stop tracking a wallet"),
        BotCommand("rename", "Rename a wallet"),
        BotCommand("threshold", "Set min USD per wallet"),
        BotCommand("help", "Help"),
    ])

    # Health check server (Railway needs a port)
    aio_app = web.Application()
    aio_app.router.add_get("/health", lambda _: web.Response(text="OK"))
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logger.info(f"Health check on port {config.PORT}")

    # Start transfer polling manager
    _poll_manager = TransferPollManager(on_transfer=on_transfer)
    await _poll_manager.start()

    # Hydrate: poll all active wallets
    active = await storage.get_all_active_wallets()
    if active:
        await _poll_manager.set_addresses(set(active))
        logger.info(f"Hydrated {len(active)} wallets into poller")


def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    tf = thread_filter
    app.add_handler(CommandHandler("start", cmd_start, filters=tf))
    app.add_handler(CommandHandler("help", cmd_help, filters=tf))
    app.add_handler(CommandHandler("menu", cmd_menu, filters=tf))
    app.add_handler(CommandHandler("list", cmd_list, filters=tf))
    app.add_handler(CommandHandler("stop", cmd_stop, filters=tf))
    app.add_handler(CommandHandler("untrack", cmd_stop, filters=tf))
    app.add_handler(CommandHandler("rename", cmd_rename, filters=tf))
    app.add_handler(CommandHandler("threshold", cmd_threshold, filters=tf))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"^0x[a-fA-F0-9]{40}") & tf,
        handle_address,
    ))

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
