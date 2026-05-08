import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable

import arkham_api
from config import ARKHAM_CHAIN_MAP

logger = logging.getLogger(__name__)

# ~30s per wallet cycle: with 2 wallets = 4 API calls/min, 20 wallets = 40/min
CYCLE_PAUSE = 30
REQUEST_GAP = 1.5  # /transfers is HEAVY endpoint: max 1 req/sec
DUST_USD = 0.10


class TransferPollManager:
    def __init__(self, on_transfer: Callable[[dict, str], Awaitable[None]]):
        self._on_transfer = on_transfer
        self._task: asyncio.Task | None = None
        self._addresses: set[str] = set()
        self._seen_ids: dict[str, set[str]] = {}
        self._last_poll: dict[str, str] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def add_address(self, address: str) -> None:
        addr = address.lower()
        self._addresses.add(addr)
        now = datetime.now(timezone.utc) - timedelta(minutes=2)
        self._last_poll[addr] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._seen_ids.setdefault(addr, set())

    async def remove_address(self, address: str) -> None:
        addr = address.lower()
        self._addresses.discard(addr)
        self._seen_ids.pop(addr, None)
        self._last_poll.pop(addr, None)

    async def set_addresses(self, addresses: set[str]) -> None:
        now = datetime.now(timezone.utc) - timedelta(minutes=2)
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for a in addresses:
            addr = a.lower()
            self._addresses.add(addr)
            if addr not in self._last_poll:
                self._last_poll[addr] = ts
            self._seen_ids.setdefault(addr, set())

    async def _poll_loop(self) -> None:
        await asyncio.sleep(3)
        while True:
            try:
                addrs = list(self._addresses)
                if not addrs:
                    await asyncio.sleep(CYCLE_PAUSE)
                    continue

                for addr in addrs:
                    if addr not in self._addresses:
                        continue
                    try:
                        await self._poll_address(addr)
                    except Exception as e:
                        logger.error(f"poll {addr[:10]}: {e}")
                    await asyncio.sleep(REQUEST_GAP)

                await asyncio.sleep(CYCLE_PAUSE)
            except asyncio.CancelledError:
                logger.info("Poll loop cancelled")
                break
            except Exception as e:
                logger.error(f"poll_loop error: {e}", exc_info=True)
                await asyncio.sleep(CYCLE_PAUSE)

    async def _poll_address(self, address: str) -> None:
        time_gte = self._last_poll.get(address)
        transfers = await arkham_api.get_transfers(address, time_gte=time_gte)
        if transfers is None:
            return

        seen = self._seen_ids.get(address, set())
        new_ts = time_gte
        new_count = 0

        for tx in transfers:
            tx_id = tx.get("id") or tx.get("transactionHash", "")
            if not tx_id or tx_id in seen:
                continue

            usd = float(tx.get("historicalUSD") or 0)
            if 0 < usd < DUST_USD:
                seen.add(tx_id)
                continue

            seen.add(tx_id)
            bt = tx.get("blockTimestamp", "")
            if bt and (not new_ts or bt > new_ts):
                new_ts = bt

            from_obj = tx.get("fromAddress") or {}
            to_obj = tx.get("toAddress") or {}
            from_addr = (from_obj.get("address") or "").lower()
            to_addr = (to_obj.get("address") or "").lower()
            chain = tx.get("chain", "ethereum")
            chain_id = ARKHAM_CHAIN_MAP.get(chain, "0x1")

            from_label = arkham_api.extract_label(from_obj)
            to_label = arkham_api.extract_label(to_obj)

            token_symbol = tx.get("tokenSymbol", "")
            unit_value = float(tx.get("unitValue") or 0)

            event = {
                "hash": tx.get("transactionHash", ""),
                "chain_id": chain_id,
                "from": from_addr,
                "to": to_addr,
                "token_symbol": token_symbol or "ETH",
                "amount": unit_value,
                "value_usd": usd,
                "from_label": from_label,
                "to_label": to_label,
            }

            try:
                await self._on_transfer(event, address)
                new_count += 1
            except Exception as e:
                logger.error(f"on_transfer error: {e}", exc_info=True)

        if new_ts and new_ts != time_gte:
            self._last_poll[address] = new_ts

        if new_count > 0:
            logger.info(f"Poll {address[:10]}: {new_count} new transfers")

        if len(seen) > 500:
            self._seen_ids[address] = set(list(seen)[-200:])
        else:
            self._seen_ids[address] = seen
