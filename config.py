import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ARKHAM_API_KEY = os.environ["ARKHAM_API_KEY"]

PORT = int(os.getenv("PORT", "8080"))
BOT_THREAD_ID = int(os.getenv("BOT_THREAD_ID", "0")) or None

ARKHAM_BASE_URL = "https://api.arkhamintelligence.com"

ARKHAM_CHAIN_MAP = {
    "ethereum": "0x1",
    "bsc": "0x38",
    "polygon": "0x89",
    "arbitrum_one": "0xa4b1",
    "arbitrum": "0xa4b1",
    "optimism": "0xa",
    "base": "0x2105",
    "avalanche": "0xa86a",
}

CHAIN_NAMES = {
    "0x1": "Ethereum",
    "0x38": "BNB Chain",
    "0x89": "Polygon",
    "0xa4b1": "Arbitrum",
    "0xa": "Optimism",
    "0x2105": "Base",
    "0xa86a": "Avalanche",
}

CHAIN_EXPLORERS = {
    "0x1": "https://etherscan.io/tx/{}",
    "0x38": "https://bscscan.com/tx/{}",
    "0x89": "https://polygonscan.com/tx/{}",
    "0xa4b1": "https://arbiscan.io/tx/{}",
    "0xa": "https://optimistic.etherscan.io/tx/{}",
    "0x2105": "https://basescan.org/tx/{}",
}
