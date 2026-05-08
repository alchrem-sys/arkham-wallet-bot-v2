import logging
from dataclasses import dataclass, field

import arkham_api
from config import ARKHAM_CHAIN_MAP

logger = logging.getLogger(__name__)

CEX_TYPES = {"cex", "exchange", "dex"}
MULTISIG_KEYWORDS = {"gnosis", "safe", "multisig", "multi-sig"}
MIXER_KEYWORDS = {"tornado", "mixer", "railgun", "aztec"}
BRIDGE_KEYWORDS = {"bridge", "wormhole", "stargate", "layerzero", "hop", "across"}


def _is_fresh(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return True
    if addr_obj.get("arkhamEntity") or addr_obj.get("arkhamLabel"):
        return False
    return True


def _is_cex(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return False
    entity = addr_obj.get("arkhamEntity") or {}
    if entity.get("type", "").lower() in CEX_TYPES:
        return True
    name = (entity.get("name") or "").lower()
    label = (addr_obj.get("arkhamLabel") or {})
    label_name = (label.get("name") or "").lower()
    cex_names = {"binance", "coinbase", "kraken", "okx", "bybit", "bitget",
                 "kucoin", "gate.io", "huobi", "htx", "mexc", "bitfinex",
                 "crypto.com", "gemini", "bitstamp"}
    for cex in cex_names:
        if cex in name or cex in label_name:
            return True
    return False


def _is_multisig(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return False
    entity = addr_obj.get("arkhamEntity") or {}
    label = addr_obj.get("arkhamLabel") or {}
    text = f"{entity.get('name', '')} {label.get('name', '')}".lower()
    return any(kw in text for kw in MULTISIG_KEYWORDS)


def _is_mixer(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return False
    entity = addr_obj.get("arkhamEntity") or {}
    label = addr_obj.get("arkhamLabel") or {}
    text = f"{entity.get('name', '')} {label.get('name', '')}".lower()
    return any(kw in text for kw in MIXER_KEYWORDS)


def _is_bridge(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return False
    entity = addr_obj.get("arkhamEntity") or {}
    label = addr_obj.get("arkhamLabel") or {}
    text = f"{entity.get('name', '')} {label.get('name', '')}".lower()
    return any(kw in text for kw in BRIDGE_KEYWORDS)


def _get_label(addr_obj: dict | None) -> str | None:
    return arkham_api.extract_label(addr_obj)


@dataclass
class FlaggedTransfer:
    tx_hash: str
    chain: str
    chain_id: str
    from_addr: str
    to_addr: str
    from_label: str | None
    to_label: str | None
    token: str
    amount: float
    usd: float
    timestamp: str
    flags: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    address: str
    entity: str | None
    total_usd: float
    total_transfers: int
    flagged: list[FlaggedTransfer]
    summary: dict[str, int] = field(default_factory=dict)


async def analyze_wallet(address: str, days: int = 7) -> AnalysisResult | None:
    from datetime import datetime, timezone, timedelta
    time_gte = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    transfers = await arkham_api.get_transfers(address, time_gte=time_gte, limit=200)
    if transfers is None:
        return None

    intel = await arkham_api.get_intelligence(address)
    entity = arkham_api.extract_entity(intel, address)

    portfolio = await arkham_api.get_portfolio(address)
    total_usd = 0.0
    if portfolio and isinstance(portfolio, dict):
        for chain_data in portfolio.values():
            if isinstance(chain_data, dict):
                for token_data in chain_data.values():
                    if isinstance(token_data, dict):
                        total_usd += float(token_data.get("usd", 0) or 0)

    watched = address.lower()
    flagged: list[FlaggedTransfer] = []
    summary: dict[str, int] = {}

    for tx in transfers:
        from_obj = tx.get("fromAddress") or {}
        to_obj = tx.get("toAddress") or {}
        from_addr = (from_obj.get("address") or "").lower()
        to_addr = (to_obj.get("address") or "").lower()
        usd = float(tx.get("historicalUSD") or 0)
        chain = tx.get("chain", "ethereum")
        chain_id = ARKHAM_CHAIN_MAP.get(chain, "0x1")

        is_out = from_addr == watched
        is_in = to_addr == watched

        flags: list[str] = []

        if is_out:
            if _is_fresh(to_obj):
                flags.append("🆕 → Fresh wallet")
            if _is_cex(to_obj):
                flags.append("🏦 → Exchange deposit")
            if _is_mixer(to_obj):
                flags.append("🌀 → Mixer")
            if _is_bridge(to_obj):
                flags.append("🌉 → Bridge")
            if _is_multisig(from_obj) and _is_fresh(to_obj):
                flags.append("🔐 Multisig → Fresh")

        if is_in:
            if _is_fresh(from_obj):
                flags.append("🆕 ← From fresh wallet")
            if _is_mixer(from_obj):
                flags.append("🌀 ← From mixer")
            if _is_multisig(from_obj):
                flags.append("🔐 ← From multisig")

        if usd >= 50000:
            flags.append("💰 Large transfer")

        if not flags:
            continue

        for f in flags:
            tag = f.split(" ")[0] + " " + f.split(" ", 1)[1] if " " in f else f
            summary[tag] = summary.get(tag, 0) + 1

        flagged.append(FlaggedTransfer(
            tx_hash=tx.get("transactionHash", ""),
            chain=chain,
            chain_id=chain_id,
            from_addr=from_addr,
            to_addr=to_addr,
            from_label=_get_label(from_obj),
            to_label=_get_label(to_obj),
            token=tx.get("tokenSymbol") or "ETH",
            amount=float(tx.get("unitValue") or 0),
            usd=usd,
            timestamp=tx.get("blockTimestamp", ""),
            flags=flags,
        ))

    return AnalysisResult(
        address=address,
        entity=entity,
        total_usd=total_usd,
        total_transfers=len(transfers),
        flagged=flagged,
        summary=summary,
    )
