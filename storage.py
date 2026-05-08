import asyncio
import logging
from upstash_redis import Redis
from config import REDIS_URL, REDIS_TOKEN

logger = logging.getLogger(__name__)

# In-memory caches to avoid Redis round-trips on hot path
_sub_cache: dict[str, tuple[float, list[int]]] = {}  # address → (ts, [chat_ids])
_SUB_CACHE_TTL = 60  # seconds


_redis_client: Redis | None = None


def _r() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis(url=REDIS_URL, token=REDIS_TOKEN)
    return _redis_client


async def add_wallet(
    chat_id: int,
    address: str,
    name: str | None = None,
    threshold: float | None = None,
) -> bool:
    address = address.lower()
    def _do():
        r = _r()
        added = r.sadd(f"bot:chat:{chat_id}:wallets", address)
        r.sadd(f"bot:wallet:{address}:subscribers", str(chat_id))
        r.sadd("bot:active_wallets", address)
        if name:
            r.set(f"bot:chat:{chat_id}:wallet:{address}:name", name)
        if threshold is not None:
            r.set(f"bot:chat:{chat_id}:wallet:{address}:threshold", str(threshold))
        return bool(added)
    result = await asyncio.to_thread(_do)
    _sub_cache.pop(address, None)
    return result


async def remove_wallet(chat_id: int, address: str) -> bool:
    """Returns True if no subscribers left for this wallet."""
    address = address.lower()
    def _do():
        r = _r()
        r.srem(f"bot:chat:{chat_id}:wallets", address)
        r.srem(f"bot:wallet:{address}:subscribers", str(chat_id))
        r.delete(f"bot:chat:{chat_id}:wallet:{address}:name")
        r.delete(f"bot:chat:{chat_id}:wallet:{address}:threshold")
        remaining = r.scard(f"bot:wallet:{address}:subscribers") or 0
        if remaining == 0:
            r.srem("bot:active_wallets", address)
            r.delete(f"bot:wallet:{address}:total_usd")
            return True
        return False
    result = await asyncio.to_thread(_do)
    _sub_cache.pop(address, None)
    return result


async def get_wallets(chat_id: int) -> list[str]:
    def _do():
        r = _r()
        return list(r.smembers(f"bot:chat:{chat_id}:wallets") or [])
    return await asyncio.to_thread(_do)


async def get_wallet_name(chat_id: int, address: str) -> str | None:
    address = address.lower()
    def _do():
        return _r().get(f"bot:chat:{chat_id}:wallet:{address}:name")
    return await asyncio.to_thread(_do)


async def get_wallet_threshold(chat_id: int, address: str) -> float | None:
    """Returns None if no threshold set (= no filter)."""
    address = address.lower()
    def _do():
        val = _r().get(f"bot:chat:{chat_id}:wallet:{address}:threshold")
        return float(val) if val else None
    return await asyncio.to_thread(_do)


async def set_wallet_name(chat_id: int, address: str, name: str) -> None:
    address = address.lower()
    def _do():
        _r().set(f"bot:chat:{chat_id}:wallet:{address}:name", name)
    await asyncio.to_thread(_do)


async def set_wallet_threshold(chat_id: int, address: str, threshold: float) -> None:
    address = address.lower()
    def _do():
        _r().set(f"bot:chat:{chat_id}:wallet:{address}:threshold", str(threshold))
    await asyncio.to_thread(_do)


async def get_subscribers(address: str) -> list[int]:
    import time
    address = address.lower()
    cached = _sub_cache.get(address)
    if cached and time.time() - cached[0] < _SUB_CACHE_TTL:
        return cached[1]

    def _do():
        subs = _r().smembers(f"bot:wallet:{address}:subscribers") or []
        return [int(s) for s in subs]
    result = await asyncio.to_thread(_do)
    _sub_cache[address] = (time.time(), result)
    return result


async def get_all_active_wallets() -> list[str]:
    def _do():
        return list(_r().smembers("bot:active_wallets") or [])
    return await asyncio.to_thread(_do)


async def count_wallets(chat_id: int) -> int:
    def _do():
        return _r().scard(f"bot:chat:{chat_id}:wallets") or 0
    return await asyncio.to_thread(_do)


async def save_stream_id(stream_id: str) -> None:
    def _do():
        _r().set("bot:moralis:stream_id", stream_id)
    await asyncio.to_thread(_do)


async def get_stream_id() -> str | None:
    def _do():
        return _r().get("bot:moralis:stream_id")
    return await asyncio.to_thread(_do)


_local_seen: set[str] = set()
_local_seen_day: str = ""


async def is_event_seen(dedup_key: str) -> bool:
    """Fast local check first, then Redis SADD for persistence across restarts."""
    global _local_seen, _local_seen_day
    import datetime
    day = datetime.datetime.utcnow().strftime("%Y%m%d")

    if day != _local_seen_day:
        _local_seen = set()
        _local_seen_day = day

    if dedup_key in _local_seen:
        return True

    set_key = f"bot:seen:{day}"
    def _do():
        try:
            r = _r()
            added = r.sadd(set_key, dedup_key)
            try:
                added_int = int(added)
            except (TypeError, ValueError):
                added_int = 1 if added else 0
            r.expire(set_key, 172800)
            return added_int == 0
        except Exception as e:
            logger.error(f"is_event_seen error: {e}")
            return False
    seen = await asyncio.to_thread(_do)
    _local_seen.add(dedup_key)
    return seen


# Backward-compatible alias for tx-level dedup
async def is_tx_seen(tx_hash: str) -> bool:
    return await is_event_seen(f"tx:{tx_hash}")


_total_cache: dict[str, tuple[float, float]] = {}  # address → (ts, value)

async def get_wallet_total_usd(address: str) -> float:
    import time
    address = address.lower()
    cached = _total_cache.get(address)
    if cached and time.time() - cached[0] < 120:
        return cached[1]
    def _do():
        val = _r().get(f"bot:wallet:{address}:total_usd")
        return float(val) if val else 0.0
    result = await asyncio.to_thread(_do)
    _total_cache[address] = (time.time(), result)
    return result


async def set_wallet_total_usd(address: str, total: float) -> None:
    import time
    address = address.lower()
    _total_cache[address] = (time.time(), total)
    def _do():
        _r().set(f"bot:wallet:{address}:total_usd", str(total), ex=86400)
    await asyncio.to_thread(_do)
