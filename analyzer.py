import logging
from dataclasses import dataclass, field

import arkham_api
from config import ARKHAM_CHAIN_MAP

logger = logging.getLogger(__name__)

MIN_USD = 10_000

CEX_TYPES = {"cex", "exchange"}
MULTISIG_KEYWORDS = {"gnosis", "safe", "multisig", "multi-sig"}
MIXER_KEYWORDS = {"tornado", "mixer", "railgun", "aztec"}
BRIDGE_KEYWORDS = {"bridge", "wormhole", "stargate", "layerzero", "hop", "across"}

NATIVE_TOKENS = {"eth", "bnb", "matic", "pol", "avax"}
STABLE_TOKENS = {"usdt", "usdc", "dai", "busd", "tusd", "usdd", "frax", "lusd", "gusd", "pyusd"}
LEGIT_TOKENS = NATIVE_TOKENS | STABLE_TOKENS | {
    "weth", "wbtc", "btcb", "steth", "wsteth", "cbeth", "reth",
    "link", "uni", "aave", "mkr", "snx", "crv", "ldo", "arb",
    "op", "pendle", "gmx", "grt", "ens", "dydx", "comp", "sushi",
    "pepe", "shib", "doge", "floki", "bonk",
    "sol", "ape", "blur", "mana", "sand",
}


def _is_legit_token(symbol: str) -> bool:
    if not symbol:
        return False
    return symbol.lower() in LEGIT_TOKENS


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


def _is_dex(addr_obj: dict | None) -> bool:
    if not addr_obj:
        return False
    entity = addr_obj.get("arkhamEntity") or {}
    label = addr_obj.get("arkhamLabel") or {}
    text = f"{entity.get('name', '')} {label.get('name', '')}".lower()
    dex_names = {"uniswap", "sushiswap", "pancakeswap", "1inch", "paraswap",
                 "0x", "curve", "balancer", "dodo", "trader joe", "camelot"}
    return any(d in text for d in dex_names)


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
    scanned_transfers: int
    flagged: list[FlaggedTransfer]
    summary: dict[str, int] = field(default_factory=dict)
    token_flows: dict[str, dict] = field(default_factory=dict)


async def analyze_wallet(address: str, days: int = 7, min_usd: float = MIN_USD) -> AnalysisResult | None:
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
    token_flows: dict[str, dict] = {}

    for tx in transfers:
        from_obj = tx.get("fromAddress") or {}
        to_obj = tx.get("toAddress") or {}
        from_addr = (from_obj.get("address") or "").lower()
        to_addr = (to_obj.get("address") or "").lower()
        usd = float(tx.get("historicalUSD") or 0)
        token = tx.get("tokenSymbol") or ""
        chain = tx.get("chain", "ethereum")
        chain_id = ARKHAM_CHAIN_MAP.get(chain, "0x1")
        amount = float(tx.get("unitValue") or 0)

        if usd < min_usd:
            continue

        if not _is_legit_token(token):
            continue

        is_out = from_addr == watched
        is_in = to_addr == watched

        # Track token flows
        tk = token.upper()
        if tk not in token_flows:
            token_flows[tk] = {"out_usd": 0, "in_usd": 0, "out_count": 0, "in_count": 0}
        if is_out:
            token_flows[tk]["out_usd"] += usd
            token_flows[tk]["out_count"] += 1
        if is_in:
            token_flows[tk]["in_usd"] += usd
            token_flows[tk]["in_count"] += 1

        # Skip DEX/router swaps — not suspicious
        if _is_dex(to_obj) or _is_dex(from_obj):
            continue

        flags: list[str] = []

        if is_out:
            if _is_cex(to_obj):
                flags.append("🏦 → CEX")
            elif _is_mixer(to_obj):
                flags.append("🌀 → Mixer")
            elif _is_bridge(to_obj):
                flags.append("🌉 → Bridge")
            elif _is_fresh(to_obj):
                if _is_multisig(from_obj):
                    flags.append("🔐 Multisig → Fresh")
                else:
                    flags.append("🆕 → Fresh wallet")

        if is_in:
            if _is_mixer(from_obj):
                flags.append("🌀 ← Mixer")
            elif _is_multisig(from_obj):
                flags.append("🔐 ← Multisig")
            elif _is_fresh(from_obj):
                flags.append("🆕 ← Fresh wallet")

        if not flags:
            continue

        for f in flags:
            summary[f] = summary.get(f, 0) + 1

        flagged.append(FlaggedTransfer(
            tx_hash=tx.get("transactionHash", ""),
            chain=chain,
            chain_id=chain_id,
            from_addr=from_addr,
            to_addr=to_addr,
            from_label=_get_label(from_obj),
            to_label=_get_label(to_obj),
            token=tk,
            amount=amount,
            usd=usd,
            timestamp=tx.get("blockTimestamp", ""),
            flags=flags,
        ))

    return AnalysisResult(
        address=address,
        entity=entity,
        total_usd=total_usd,
        total_transfers=len(transfers),
        scanned_transfers=sum(1 for tx in transfers if float(tx.get("historicalUSD") or 0) >= min_usd),
        flagged=flagged,
        summary=summary,
        token_flows=token_flows,
    )
