import logging
import time
from typing import Optional
import httpx
from config import ARKHAM_API_KEY, ARKHAM_BASE_URL

logger = logging.getLogger(__name__)

HEADERS = {"API-Key": ARKHAM_API_KEY}


async def get_portfolio(address: str) -> Optional[dict]:
    now_ms = int(time.time() * 1000)
    url = f"{ARKHAM_BASE_URL}/portfolio/address/{address}?time={now_ms}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_portfolio({address}): {e}")
            return None


async def get_balances(address: str, chains: str = "") -> Optional[list]:
    url = f"{ARKHAM_BASE_URL}/balances/address/{address}"
    if chains:
        url += f"?chains={chains}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_balances({address}): {e}")
            return None


async def get_intelligence(address: str) -> Optional[dict]:
    url = f"{ARKHAM_BASE_URL}/intelligence/address/{address}/all"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_intelligence({address}): {e}")
            return None


def extract_entity(intel: dict | None, address: str) -> str | None:
    """Extract entity/label name from intelligence response."""
    if not intel:
        return None
    for chain_data in intel.values():
        if not isinstance(chain_data, dict):
            continue
        label = chain_data.get("arkhamLabel")
        if label and label.get("name"):
            return label["name"]
        entity = chain_data.get("arkhamEntity")
        if entity and entity.get("name"):
            return entity["name"]
    return None
