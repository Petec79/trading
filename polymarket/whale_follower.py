#!/usr/bin/env python3
"""
Whale Follower Bot — Polymarket
Mirrors top-performing wallets with Quarter Kelly position sizing.
Category-filtered: only copy dominance patterns per category.

Reference: @LunarResearcher alpha (Mar 30, 2026)
Bot: Midas (Paperclip)
"""

import json
import time
import sqlite3
import math
import os
import requests
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

# =============================================================================
# CONFIG
# =============================================================================

POLYMARKET_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
SUBGRAPH = "https://api.thegraph.com/subgraphs/name/polymarket/matic-markets"

DB_PATH = Path.home() / ".polyterm" / "whale_follower.db"
WALLET_CACHE_TTL = 3600  # 1 hour
MAX_CHALLENGE_ROUNDS = 3
PAPER_TRADE = True  # Set False when live

# Starting capital
BANKROLL = float(os.environ.get("POLY_BANKROLL", "200.0"))  # $200 paper

# =============================================================================
# KELLY CRITERION — Quarter Kelly
# =============================================================================

def quarter_kelly(win_rate: float, odds: float) -> float:
    """
    Full Kelly: f* = (p * b - q) / b
    where b = payout ratio (1 for even money)
          q = 1 - p
    
    Quarter Kelly: divide by 4 (risk management)
    
    Args:
        win_rate: historical win rate (0.0 - 1.0)
        odds: payout ratio (e.g. 2.0 = risk $1 to win $2)
    
    Returns:
        fraction of bankroll to bet (0.0 - 1.0)
    """
    p = win_rate
    q = 1 - p
    b = odds - 1  # convert decimal to Kelly b
    
    if b <= 0:
        return 0.0
    
    full_kelly = (p * b - q) / b
    
    if full_kelly <= 0:
        return 0.0
    
    quarter = full_kelly / 4.0
    return min(quarter, 0.25)  # Cap at 25% of bankroll per trade


def bet_size(bankroll: float, kelly_fraction: float, odds: float) -> float:
    """
    Calculate dollar bet size.
    
    Kelly fraction * bankroll = units
    We want units to equal our edge in dollars.
    
    If kelly_fraction=0.10 and bankroll=$200, bet=$20
    """
    return round(kelly_fraction * bankroll, 2)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Wallet:
    address: str
    category: str          # 'crypto', 'sports', 'geopolitics', 'mixed'
    dominance_score: float  # 0-1, how category-specialized
    total_pnl: float
    win_rate: float
    total_trades: int
    last_active: str
    challenge_rounds: int = 0  # times rejected by challenges


@dataclass
class Market:
    slug: str
    question: str
    category: str
    volume: float
    liquidity: float
    end_date: str
    yes_price: float  # 0-1
    no_price: float   # 0-1
    tags: list = field(default_factory=list)
    outcome_id: str = ""  # PMXT outcome ID for orderbook lookups


@dataclass
class Signal:
    wallet_address: str
    market_slug: str
    market_question: str
    side: str         # 'yes' or 'no'
    entry_price: float
    current_price: float
    kelly_fraction: float
    bet_size: float
    confidence: str   # 'high', 'medium', 'low'
    category: str
    timestamp: str
    challenge_rounds: int = 0
    status: str = 'active'  # active, accepted, rejected, expired
    end_date: str = ""  # market end date for resolution


# =============================================================================
# DATABASE
# =============================================================================

def init_db():
    """Create SQLite schema if not exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            address TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            dominance_score REAL DEFAULT 0.5,
            total_pnl REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            last_active TEXT,
            updated_at TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS tracked_markets (
            slug TEXT PRIMARY KEY,
            question TEXT,
            category TEXT,
            volume REAL DEFAULT 0,
            liquidity REAL DEFAULT 0,
            end_date TEXT,
            yes_price REAL DEFAULT 0.5,
            no_price REAL DEFAULT 0.5,
            tags TEXT,
            updated_at TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT,
            market_slug TEXT,
            market_question TEXT,
            side TEXT,
            entry_price REAL,
            current_price REAL,
            kelly_fraction REAL,
            bet_size REAL,
            confidence TEXT,
            category TEXT,
            timestamp TEXT,
            challenge_rounds INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            UNIQUE(wallet_address, market_slug, timestamp)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            wallet_address TEXT,
            market_slug TEXT,
            side TEXT,
            size REAL,
            price REAL,
            timestamp TEXT,
            pnl REAL,
            paper_trade INTEGER DEFAULT 1,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS challenge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            challenge_reason TEXT,
            outcome TEXT,
            timestamp TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.commit()
    conn.close()


def get_db_connection():
    return sqlite3.connect(str(DB_PATH))


# =============================================================================
# API: THE GRAPH — Whale Wallet Discovery
# =============================================================================

def gql_query(query: str, variables: dict = None, retries: int = 3) -> Optional[dict]:
    """Execute a GraphQL query against The Graph subgraph."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
        
    for attempt in range(retries):
        try:
            resp = requests.post(SUBGRAPH, json=payload, timeout=30)
            if resp.ok:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2 ** attempt)
            else:
                print(f"  GraphQL HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            print(f"  GraphQL attempt {attempt+1} error: {e}")
            time.sleep(2)
    return None


def fetch_top_wallets(limit: int = 20) -> list:
    """
    Fetch largest traders by volume from The Graph.
    Returns wallet addresses with their trade stats.
    """
    query = """
    {
      fpmmTrades(
        first: 200
        orderBy: timestamp
        orderDirection: desc
      ) {
        id
        volume
        price
        side
        timestamp
        creator {
          id
        }
        market {
          question
          slug
          tags
        }
      }
    }
    """
    
    result = gql_query(query)
    if not result or 'data' not in result:
        print("Failed to fetch trades from The Graph")
        return []
    
    trades = result['data'].get('fpmmTrades', [])
    
    # Aggregate by wallet
    wallet_volumes = {}
    wallet_stats = {}
    
    for trade in trades:
        wallet = trade['creator']['id'].lower()
        vol = float(trade.get('volume', 0) or 0)
        price = float(trade.get('price', 0.5) or 0.5)
        side = trade.get('side', '')
        market_slug = trade.get('market', {}).get('slug', '')
        market_tags = trade.get('market', {}).get('tags', [])
        ts = trade.get('timestamp', '')
        
        if wallet == "0x0000000000000000000000000000000000000000":
            continue
        
        if wallet not in wallet_volumes:
            wallet_volumes[wallet] = 0
            wallet_stats[wallet] = {
                'trades': [],
                'volume': 0,
                'wins': 0,
                'losses': 0,
                'total_pnl': 0,
                'categories': set(),
            }
        
        wallet_volumes[wallet] += vol
        wallet_stats[wallet]['volume'] += vol
        wallet_stats[wallet]['trades'].append({
            'volume': vol,
            'price': price,
            'side': side,
            'slug': market_slug,
            'tags': market_tags,
            'timestamp': ts,
        })
        
        # Rough PnL estimate
        if side.lower() == 'buy':
            # Bought YES — win if price went to 1
            estimated_pnl = vol * (1 - price) if price < 1 else 0
            wallet_stats[wallet]['total_pnl'] += estimated_pnl
            if price > 0.55:
                wallet_stats[wallet]['wins'] += 1
            else:
                wallet_stats[wallet]['losses'] += 1
        else:
            # Bought NO — win if price went to 0
            estimated_pnl = vol * price if price < 0.45 else 0
            wallet_stats[wallet]['total_pnl'] += estimated_pnl
            if price < 0.45:
                wallet_stats[wallet]['wins'] += 1
            else:
                wallet_stats[wallet]['losses'] += 1
    
    # Sort by volume and return top N
    sorted_wallets = sorted(
        wallet_volumes.items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]
    
    return [
        {
            'address': addr,
            'volume': vol,
            **wallet_stats[addr]
        }
        for addr, vol in sorted_wallets
    ]


def infer_wallet_category(wallet_stats: dict) -> tuple[str, float]:
    """
    Infer wallet's dominant category from their trade tags.
    Returns (category, dominance_score).
    """
    all_tags = []
    for trade in wallet_stats.get('trades', []):
        tags = trade.get('tags', [])
        if isinstance(tags, list):
            all_tags.extend([t.lower() for t in tags])
        elif isinstance(tags, str):
            all_tags.extend([t.strip().lower() for t in tags.split(',')])
    
    if not all_tags:
        return 'mixed', 0.3
    
    # Count tag frequencies
    tag_counts = {}
    for tag in all_tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    
    total = len(all_tags)
    top_tag, top_count = max(tag_counts.items(), key=lambda x: x[1])
    dominance = top_count / total
    
    # Map to our categories
    crypto_keywords = ['crypto', 'bitcoin', 'ethereum', 'defi', 'nft', 'web3', 'blockchain']
    sports_keywords = ['sports', 'football', 'basketball', 'soccer', 'nfl', 'nba', 'ufc', 'tennis', 'baseball', 'hockey', 'golf']
    geopolitics_keywords = ['politics', 'election', 'government', 'war', 'trade', 'sanctions', 'diplomacy', 'congress', 'parliament', 'g7', 'g20']
    
    if any(k in top_tag for k in crypto_keywords):
        cat = 'crypto'
    elif any(k in top_tag for k in sports_keywords):
        cat = 'sports'
    elif any(k in top_tag for k in geopolitics_keywords):
        cat = 'geopolitics'
    else:
        cat = 'other'
    
    # Boost dominance if very high (>70%)
    if dominance > 0.7:
        return cat, dominance
    
    return cat, min(dominance, 0.5)


def save_wallets(wallets: list):
    """Persist wallets to SQLite."""
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    for w in wallets:
        wins = w.get('wins', 0)
        losses = w.get('losses', 0)
        total_trades = wins + losses
        win_rate = wins / total_trades if total_trades > 0 else 0
        
        cat, dom = infer_wallet_category(w)
        
        c.execute("""
            INSERT OR REPLACE INTO wallets 
            (address, category, dominance_score, total_pnl, win_rate, total_trades, last_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            w['address'],
            cat,
            dom,
            w.get('total_pnl', 0),
            win_rate,
            total_trades,
            w['trades'][-1]['timestamp'] if w.get('trades') else now,
            now
        ))
    
    conn.commit()
    conn.close()


# =============================================================================
# API: Gamma — Market Data
# =============================================================================

def fetch_active_markets(limit: int = 50) -> list[Market]:
    """Fetch active markets via PMXT (unified prediction market API).
    Replaces Gamma API (GAMMA_API) which has become stale/unreliable.
    """
    try:
        # Import here to avoid circular imports and ensure PMXT path is set
        import sys
        from pathlib import Path
        PMXT_PATH = "/home/openclaw/.local/lib/python3.12/site-packages"
        if PMXT_PATH not in sys.path:
            sys.path.insert(0, PMXT_PATH)
        
        from pmxt_integration import fetch_active_markets as pmxt_fetch_active_markets
        
        # PMXT returns pmxt_integration.Market objects — convert to whale_follower.Market
        pmxt_markets = pmxt_fetch_active_markets(limit=limit)
        
        # Also fetch orderbook data to get outcome_ids (needed for trading)
        poly = None
        try:
            from pmxt_integration import get_polymarket
            poly = get_polymarket()
        except Exception:
            pass
        
        markets = []
        for m in pmxt_markets:
            try:
                market = Market(
                    slug=m.slug,
                    question=m.question,
                    category=m.category or 'unknown',
                    volume=m.volume,
                    liquidity=m.liquidity,
                    end_date=m.end_date,
                    yes_price=m.yes_price,
                    no_price=m.no_price,
                    tags=m.tags or [],
                )
                markets.append(market)
            except (ValueError, TypeError):
                continue
        return markets
    except Exception as e:
        print(f"fetch_active_markets error: {e}")
        return []


def get_orderbook(slug: str) -> Optional[dict]:
    """Get orderbook for a market slug via PMXT.
    Replaces Gamma API (GAMMA_API/orderbook/{slug}).
    """
    try:
        import sys
        PMXT_PATH = "/home/openclaw/.local/lib/python3.12/site-packages"
        if PMXT_PATH not in sys.path:
            sys.path.insert(0, PMXT_PATH)
        
        from pmxt_integration import get_orderbook as pmxt_get_orderbook
        return pmxt_get_orderbook(slug)
    except Exception as e:
        print(f"orderbook error for {slug}: {e}")
    return None


# =============================================================================
# API: CLOB — Place Orders
# =============================================================================

def get_markets_from_clob(limit: int = 20) -> Optional[dict]:
    """Fetch markets from CLOB API."""
    try:
        resp = requests.get(
            f"{POLYMARKET_API}/markets",
            params={"limit": limit, "closed": "false"},
            timeout=15
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"CLOB markets error: {e}")
    return None


# =============================================================================
# SIGNAL GENERATION — Fallback (no whale wallet data needed)
# =============================================================================

def generate_fallback_signals() -> list[Signal]:
    """
    Fallback signal generator that works without The Graph whale data.
    Strategy: Momentum + mean reversion hybrid on high-volume active markets.
    
    Logic:
    1. Fetch high-volume active markets from Gamma API
    2. Filter by liquidity and volume thresholds
    3. Apply simple momentum/mean-reversion signals:
       - If YES price is between 0.45-0.55 and trending toward one side, 
         bet on the trending direction (crowd wisdom)
       - If YES price is 0.05-0.15 OR 0.85-0.95 (near edges), 
         bet on mean reversion (fade the extreme)
    4. Apply Kelly sizing with assumed 55% win rate
    """
    import random
    
    print("\n=== Fallback Signal Generation (no whale data) ===")
    
    # Fetch markets from Gamma
    markets = fetch_active_markets(limit=50)
    
    if not markets:
        print("No active markets from Gamma API.")
        return []
    
    # Filter: high volume, not ending soon
    now = datetime.utcnow()
    filtered = []
    for m in markets:
        if m.volume < 5000:
            continue
        try:
            end = datetime.fromisoformat(m.end_date.replace('Z', '+00:00'))
            if (end - now).total_seconds() < 3600:  # Skip if ending in <1hr
                continue
        except (ValueError, TypeError):
            pass
        filtered.append(m)
    
    if not filtered:
        print("No markets meeting volume/time criteria.")
        return []
    
    print(f"Evaluating {len(filtered)} high-volume markets for fallback signals...")
    
    signals = []
    for market in filtered:
        try:
            yes_price = market.yes_price
            no_price = market.no_price
            
            # Skip very low liquidity or weird prices
            if yes_price < 0.01 or yes_price > 0.99:
                continue
            
            side = None
            confidence = 'medium'
            
            # Mean reversion: price is near edges (institutions have an edge fading extremes)
            if yes_price >= 0.88:
                # 88%+ YES — likely overpaying, fade it with NO
                side = 'no'
                confidence = 'high'
            elif yes_price <= 0.12:
                # 12%- YES — likely underpriced, bet YES
                side = 'yes'
                confidence = 'high'
            elif 0.42 <= yes_price <= 0.58:
                # Near 50/50 — momentum bet on whichever side has more conviction
                # Use a simple random-ish bias toward YES (slight positive drift)
                side = 'yes'
                confidence = 'medium'
            elif yes_price > 0.65:
                # 65-88% YES — high confidence YES
                side = 'yes'
                confidence = 'high'
            elif yes_price < 0.35:
                # 12-35% YES — moderate NO
                side = 'no'
                confidence = 'medium'
            else:
                # 58-65% — skip (no clear edge)
                continue
            
            # Calculate Kelly: assume 55% win rate for fallback (conservative)
            win_rate = 0.55
            if side == 'yes':
                odds = 1.0 / yes_price
            else:
                odds = 1.0 / no_price
            
            kelly_frac = quarter_kelly(win_rate, odds)
            
            if kelly_frac <= 0:
                continue
            
            bet = bet_size(BANKROLL, kelly_frac, odds)
            
            # Skip if bet too small
            if bet < 1.0:
                continue
            
            # Create a synthetic wallet address for fallback signals
            fallback_address = f"fallback_{market.slug[:16]}"
            
            sig = Signal(
                wallet_address=fallback_address,
                market_slug=market.slug,
                market_question=market.question,
                side=side,
                entry_price=yes_price if side == 'yes' else no_price,
                current_price=yes_price,
                kelly_fraction=kelly_frac,
                bet_size=bet,
                confidence=confidence,
                category='fallback',
                timestamp=datetime.utcnow().isoformat(),
                challenge_rounds=0,
                status='active',
                end_date=market.end_date,
            )
            signals.append(sig)
            print(f"  FALLBACK SIGNAL: {side.upper()} @ {sig.entry_price:.2f} | ${bet:.2f} on {market.slug[:40]}")
            
        except Exception as e:
            continue
    
    print(f"Generated {len(signals)} fallback signals")
    return signals


# =============================================================================
# SIGNAL GENERATION — Core Logic
# =============================================================================

def generate_signals() -> list[Signal]:
    """
    Core strategy: 
    1. Load tracked wallets
    2. Get current markets
    3. Cross-reference: which wallets are active in which markets?
    4. Apply Kelly sizing and challenge rounds filter
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Load wallets (only high-dominance ones)
    c.execute("""
        SELECT address, category, dominance_score, win_rate, total_trades, challenge_rounds
        FROM wallets
        WHERE dominance_score > 0.5
        AND challenge_rounds < ?
        ORDER BY total_pnl DESC, win_rate DESC
        LIMIT 10
    """, (MAX_CHALLENGE_ROUNDS,))
    
    wallets = [
        {
            'address': row[0],
            'category': row[1],
            'dominance_score': row[2],
            'win_rate': row[3],
            'total_trades': row[4],
            'challenge_rounds': row[5],
        }
        for row in c.fetchall()
    ]
    conn.close()
    
    if not wallets:
        print("No high-dominance wallets tracked yet. Trying fallback strategy...")
        return generate_fallback_signals()

    print(f"\n=== Signal Generation ===")
    print(f"Evaluating {len(wallets)} wallets against active markets...")
    
    # Fetch current markets
    markets = fetch_active_markets(limit=30)
    
    if not markets:
        print("No active markets found.")
        return []
    
    # Filter markets by volume (>$10k) and not ending soon
    markets = [m for m in markets if m.volume > 10000]
    now = datetime.utcnow()
    
    signals = []
    
    for wallet in wallets:
        # Check which markets this wallet has traded in
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT market_slug FROM signals
            WHERE wallet_address = ? AND status = 'active'
        """, (wallet['address'],))
        already_tracked = set(row[0] for row in c.fetchall())
        conn.close()
        
        for market in markets:
            if market.slug in already_tracked:
                continue
            
            # Match: wallet category vs market category/tags
            if not category_match(wallet['category'], market):
                continue
            
            # Skip if market ending soon (<24h)
            try:
                end = datetime.fromisoformat(market.end_date.replace('Z', '+00:00'))
                if (end - now).total_seconds() < 86400:
                    continue
            except (ValueError, TypeError):
                pass
            
            # Calculate Kelly fraction
            win_rate = wallet['win_rate'] or 0.5
            
            # Use YES price as odds proxy
            if market.yes_price > 0.02:
                odds = 1 / market.yes_price  # e.g., 0.40 -> 2.5x
            else:
                odds = 10.0
            
            kelly_frac = quarter_kelly(win_rate, odds)
            
            if kelly_frac <= 0:
                continue
            
            # Minimum confidence threshold
            if wallet['dominance_score'] < 0.5:
                confidence = 'low'
            elif wallet['dominance_score'] < 0.7:
                confidence = 'medium'
            else:
                confidence = 'high'
            
            bet = bet_size(BANKROLL, kelly_frac, odds)
            
            # Skip if bet too small (<$1)
            if bet < 1.0:
                continue
            
            signal = Signal(
                wallet_address=wallet['address'],
                market_slug=market.slug,
                market_question=market.question,
                side='yes',  # whales typically buy YES
                entry_price=market.yes_price,
                current_price=market.yes_price,
                kelly_fraction=kelly_frac,
                bet_size=bet,
                confidence=confidence,
                category=wallet['category'],
                timestamp=datetime.utcnow().isoformat(),
                challenge_rounds=wallet['challenge_rounds'],
                status='active',
                end_date=market.end_date,
            )
            
            signals.append(signal)
    
    return signals


def category_match(wallet_category: str, market: Market) -> bool:
    """Check if a wallet's category matches a market's tags."""
    tags = [t.lower() for t in market.tags] if market.tags else []
    question = market.question.lower()
    category = market.category.lower()
    
    all_text = ' '.join(tags) + ' ' + question + ' ' + category
    
    crypto_keywords = ['crypto', 'bitcoin', 'ethereum', 'defi', 'nft', 'web3', 'blockchain', 'token']
    sports_keywords = ['sports', 'football', 'basketball', 'soccer', 'nfl', 'nba', 'ufc', 'tennis', 'baseball', 'hockey', 'golf', 'match', 'win', 'lose', 'score']
    geopolitics_keywords = ['politics', 'election', 'government', 'war', 'trade', 'sanctions', 'diplomacy', 'congress', 'parliament', 'g7', 'g20', 'president', 'minister', 'vote', 'policy']
    
    if wallet_category == 'crypto':
        return any(k in all_text for k in crypto_keywords) or 'crypto' in category
    elif wallet_category == 'sports':
        return any(k in all_text for k in sports_keywords) or 'sports' in category
    elif wallet_category == 'geopolitics':
        return any(k in all_text for k in geopolitics_keywords) or any(k in geopolitics_keywords for k in tags)
    
    return True  # mixed wallets can trade anything


# =============================================================================
# CHALLENGE ROUNDS — Reject signals that don't pass scrutiny
# =============================================================================

CHALLENGE_PROMPT = """You are a quantitative analyst reviewing a Polymarket trading signal.

A whale wallet (top performer) has signaled a trade. Evaluate whether to follow it.

Signal:
- Market: {question}
- Side: {side} @ ${price}
- Wallet category: {category}
- Win rate: {win_rate}
- Confidence: {confidence}
- Kelly fraction: {kelly_frac:.2%}
- Suggested bet: ${bet}

Challenge questions (answer each):
1. Is the market resolving within 48h? If yes, lower confidence.
2. Is volume >$50k? If no, avoid.
3. Is this in the wallet's DOMINANT category? If no, reject.
4. Is the Kelly fraction reasonable (<15%)? If over 20%, reject.

Return JSON only:
{{"accept": true/false, "reason": "brief explanation"}}
"""


def run_challenges(signals: list[Signal]) -> list[Signal]:
    """
    Run challenge rounds on signals.
    If a signal is challenged and rejected 3 times, it's dropped.
    """
    print(f"\n=== Challenge Rounds ({len(signals)} signals) ===")
    
    accepted = []
    
    for sig in signals:
        challenges = 0
        rejected = False
        
        for round_num in range(1, MAX_CHALLENGE_ROUNDS + 1):
            # In production: call Claude API here
            # For now: apply rule-based challenges
            reason = None
            
            # Rule 1: Price sanity check
            if sig.entry_price < 0.05 or sig.entry_price > 0.95:
                reason = f"Price {sig.entry_price:.2f} too extreme"
                challenges += 1
            
            # Rule 2: Kelly too aggressive
            elif sig.kelly_fraction > 0.15:
                reason = f"Kelly {sig.kelly_fraction:.1%} exceeds 15% threshold"
                challenges += 1
            
            # Rule 3: Low dominance wallet in this category
            elif sig.confidence == 'low':
                reason = f"Low confidence ({sig.confidence}) wallet"
                challenges += 1
            
            # Rule 4: Bet size vs bankroll sanity
            elif sig.bet_size > BANKROLL * 0.25:
                reason = f"Bet ${sig.bet_size} exceeds 25% of bankroll"
                challenges += 1
            
            if reason:
                print(f"  Round {round_num}: REJECT {sig.market_slug[:40]} — {reason}")
                sig.challenge_rounds = round_num
                log_challenge(sig, reason, 'rejected')
        
        if challenges == 0:
            sig.status = 'accepted'
            accepted.append(sig)
            print(f"  ACCEPTED: {sig.market_slug[:40]} — {sig.side} @ {sig.entry_price:.2f} | ${sig.bet_size:.2f}")
        else:
            sig.status = 'rejected'
            # Update wallet challenge rounds (skip for fallback signals)
            if not sig.wallet_address.startswith('fallback_'):
                update_wallet_challenges(sig.wallet_address)
    
    return accepted


def log_challenge(signal: Signal, reason: str, outcome: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO challenge_log (signal_id, challenge_reason, outcome, timestamp)
        VALUES (?, ?, ?, ?)
    """, (0, reason, outcome, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def update_wallet_challenges(wallet_address: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE wallets SET challenge_rounds = challenge_rounds + 1
        WHERE address = ?
    """, (wallet_address,))
    conn.commit()
    conn.close()


# =============================================================================
# EXECUTION — Paper Trading
# =============================================================================

def execute_signals(signals: list[Signal]):
    """Execute accepted signals (paper trade or real)."""
    if PAPER_TRADE:
        print(f"\n=== PAPER TRADE: {len(signals)} signals ===")
    else:
        print(f"\n=== LIVE TRADE: {len(signals)} signals ===")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    for sig in signals:
        # Save signal
        c.execute("""
            INSERT INTO signals 
            (wallet_address, market_slug, market_question, side, entry_price, 
             current_price, kelly_fraction, bet_size, confidence, category, 
             timestamp, challenge_rounds, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sig.wallet_address, sig.market_slug, sig.market_question, sig.side,
            sig.entry_price, sig.current_price, sig.kelly_fraction, sig.bet_size,
            sig.confidence, sig.category, sig.timestamp, sig.challenge_rounds, sig.status
        ))
        
        signal_id = c.lastrowid
        
        # Record trade
        c.execute("""
            INSERT INTO trades 
            (signal_id, wallet_address, market_slug, side, size, price, timestamp, pnl, paper_trade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_id, sig.wallet_address, sig.market_slug, sig.side,
            sig.bet_size, sig.entry_price, sig.timestamp, 0.0, 1 if PAPER_TRADE else 0
        ))
        
        print(f"  {'[PAPER]' if PAPER_TRADE else '[LIVE]'} {sig.side.upper()} ${sig.bet_size:.2f} on {sig.market_slug[:50]}")
    
    conn.commit()
    conn.close()


# =============================================================================
# STATUS REPORT
# =============================================================================

def print_status():
    """Print current bot status."""
    conn = get_db_connection()
    c = conn.cursor()
    
    print("\n" + "="*60)
    print("WHALE FOLLOWER BOT STATUS")
    print("="*60)
    
    # Wallet count
    c.execute("SELECT COUNT(*) FROM wallets")
    wallet_count = c.fetchone()[0]
    print(f"\nTracked Wallets: {wallet_count}")
    
    # Wallet breakdown by category
    c.execute("""
        SELECT category, COUNT(*), AVG(win_rate), AVG(dominance_score)
        FROM wallets GROUP BY category
    """)
    print("\nBy Category:")
    for row in c.fetchall():
        print(f"  {row[0]:15} {row[1]:3} wallets | avg win rate: {row[2]:.1%} | avg dominance: {row[3]:.1%}")
    
    # Active signals
    c.execute("SELECT COUNT(*) FROM signals WHERE status='active'")
    active = c.fetchone()[0]
    print(f"\nActive Signals: {active}")
    
    # Recent trades
    c.execute("""
        SELECT COUNT(*), SUM(size), SUM(pnl) FROM trades
        WHERE timestamp > datetime('now', '-7 days')
    """)
    row = c.fetchone()
    print(f"\n7-Day Trades: {row[0]} | Volume: ${row[1]:.2f} | PnL: ${row[2]:.2f}")
    
    # Config
    c.execute("SELECT value FROM config WHERE key='BANKROLL'")
    bankroll_row = c.fetchone()
    bankroll = float(bankroll_row[0]) if bankroll_row else BANKROLL
    print(f"Bankroll: ${bankroll:.2f} | Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")
    
    conn.close()
    print()


# =============================================================================
# MAIN — COMMANDS
# =============================================================================

def cmd_discover():
    """Discover and track top wallets."""
    print("\n=== WALLET DISCOVERY ===")
    wallets = fetch_top_wallets(limit=20)
    print(f"Found {len(wallets)} wallets")
    
    for w in wallets[:5]:
        cat, dom = infer_wallet_category(w)
        wins = w.get('wins', 0)
        losses = w.get('losses', 0)
        total = wins + losses
        wr = wins/total if total > 0 else 0
        print(f"\n  {w['address'][:10]}... | vol: ${w['volume']:,.0f}")
        print(f"    Category: {cat} (dominance: {dom:.0%}) | WR: {wr:.0%} ({wins}W/{losses}L)")
    
    save_wallets(wallets)
    print(f"\nSaved {len(wallets)} wallets to DB")


def cmd_signals():
    """Generate and execute trading signals."""
    signals = generate_signals()
    print(f"\nGenerated {len(signals)} signals")
    
    accepted = run_challenges(signals)
    print(f"\n{len(accepted)} signals passed challenge rounds")
    
    if accepted:
        execute_signals(accepted)
    else:
        print("No signals passed — holding capital.")


def cmd_status():
    """Print bot status."""
    print_status()


def cmd_wallets():
    """List tracked wallets."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT address, category, dominance_score, win_rate, total_trades, total_pnl, challenge_rounds
        FROM wallets ORDER BY total_pnl DESC
    """)
    rows = c.fetchall()
    conn.close()
    
    print(f"\n{'Address':<12} {'Category':<12} {'Dom':>5} {'WR':>6} {'Trades':>6} {'PnL':>10} {'Chall':>5}")
    print("-"*62)
    for r in rows:
        print(f"{r[0][:10]:<12} {r[1]:<12} {r[2]:>5.0%} {r[3]:>6.0%} {r[4]:>6} {r[5]:>10.0f} {r[6]:>5}")


def cmd_help():
    print("""
Whale Follower Bot — Commands
=============================
python whale_follower.py discover  — Scan The Graph for top wallets
python whale_follower.py signals   — Generate and execute trading signals
python whale_follower.py status    — Print bot status and stats
python whale_follower.py wallets   — List tracked wallets
python whale_follower.py test      — Run API connectivity tests

Environment Variables:
  POLY_BANKROLL=200   Starting capital (default $200)
""")


def cmd_test():
    """Test API connectivity."""
    print("\n=== API Connectivity Test ===")
    
    print("\n1. The Graph (wallet data):")
    result = gql_query("{ fpmmTrades(first: 1) { id volume } }")
    print(f"   {'OK' if result else 'FAIL'}")
    
    print("\n2. Gamma API (market data):")
    try:
        resp = requests.get(f"{GAMMA_API}/events?limit=1&closed=false", timeout=10)
        print(f"   {'OK' if resp.ok else f'FAIL: {resp.status_code}'}")
    except Exception as e:
        print(f"   FAIL: {e}")
    
    print("\n3. CLOB API (orderbook):")
    try:
        resp = requests.get(f"{POLYMARKET_API}/markets?limit=1", timeout=10)
        print(f"   {'OK' if resp.ok else f'FAIL: {resp.status_code}'}")
    except Exception as e:
        print(f"   FAIL: {e}")
    
    print("\n4. SQLite DB:")
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM wallets")
        count = c.fetchone()[0]
        print(f"   OK — {count} wallets stored")
        conn.close()
    except Exception as e:
        print(f"   FAIL: {e}")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys
    
    init_db()
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    
    if cmd == "discover":
        cmd_discover()
    elif cmd == "signals":
        cmd_signals()
    elif cmd == "status":
        cmd_status()
    elif cmd == "wallets":
        cmd_wallets()
    elif cmd == "test":
        cmd_test()
    else:
        cmd_help()
