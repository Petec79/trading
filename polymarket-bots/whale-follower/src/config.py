"""
Whale Follower Bot — Configuration
Quarter Kelly position sizing with category-filtered whale mirroring.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
STATE_FILE = BASE_DIR / "state" / "portfolio.json"
LOG_FILE = BASE_DIR / "logs" / "trades.csv"
DATA_DIR = BASE_DIR / "data"

# ─── Polymarket API ────────────────────────────────────────────────────────────
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
GRAPH_API = "https://api.thegraph.com/subgraphs/name/polymarket/matic-markets"

# ─── Kelly Sizing ──────────────────────────────────────────────────────────────
KELLY_FRACTION = 0.25          # Quarter Kelly = full Kelly ÷ 4
MAX_BET_FRACTION = 0.10       # Never bet more than 10% of bankroll on one idea
MIN_EV_THRESHOLD = 0.05        # Skip trades with < 5% expected value
POLL_INTERVAL_SECONDS = 60    # Check for new whale trades every 60s

# ─── Approved Whales (from MID-19 research) ────────────────────────────────────
# Format: { name: (wallet_address, category, min_kelly_size_usd) }
APPROVED_WHALES = {
    "HorizonSplendidView": {
        "address": "0x8b3e1c8e6e9b1c2d4f5a6b7c8d9e0f1a2b3c4d5e",
        "category": "crypto_macro",
        "description": "High-frequency crypto/macro, $4M cumulative PnL, Sharpe ~3.1",
        "min_size_usd": 200,     # Only follow moves > $200
        "max_copy_usd": 5_000,   # Cap copy size at $5K per trade
    },
    "beachboy4": {
        "address": "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        "category": "sports",
        "description": "Sports betting niche, $6.12M in ONE day, Sharpe ~4.2",
        "min_size_usd": 500,
        "max_copy_usd": 2_000,
    },
}

# ─── Wallet Address Book ───────────────────────────────────────────────────────
# Maps clean names to contract addresses used by The Graph / CLOB
WHALE_ADDRESSES = {v["name"]: v["address"] for k, v in {"HorizonSplendidView": {"name": "HorizonSplendidView", "address": APPROVED_WHALES["HorizonSplendidView"]["address"]}, "beachboy4": {"name": "beachboy4", "address": APPROVED_WHALES["beachboy4"]["address"]}}.items()}

# ─── Risk Controls ─────────────────────────────────────────────────────────────
MAX_CONCURRENT_TRADES = 5
MAX_DAILY_TRADES = 20
CIRCUIT_BREAKER_LOSS_PCT = 0.10   # Stop if portfolio drops 10% in a day
MAX_CHALLENGE_ROUNDS = 3          # Max rounds to challenge a whale signal
