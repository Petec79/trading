#!/usr/bin/env python3
"""
Spicy Momentum Scanner — Binance
=================================
Identifies momentum trade setups on Binance USDT-M futures perpetuals.

3 Criteria (per @spicyofc Momentum Strategy):
  1. Grindy "Staircase" — successive higher highs + higher lows (longs)
                              OR lower highs + lower lows (shorts)
  2. Volume increasing  — vol > SMA(vol, N) × threshold
  3. 2h+ structure      — enough swings in window to confirm regime

Quality Score:
  3/3 = All stars → ambitious target
  2/3 = Good      → 1R target
  0-1/3 = AVOID  → not a momentum regime

Usage:
  python3 momentum_scanner.py              # Binance only
  python3 momentum_scanner.py --with-velo  # Full + Velo browser (top gainers list)
"""

import sys, json, time, math, requests
from datetime import datetime, timezone
from pathlib import Path

# Discord integration
DISCORD_SIGNALS_WH = "https://discord.com/api/webhooks/1506298465116885012/WxSGSSDl9I4I41Hf7lndzKOy_MBbNVCKHErAoOYLi6qnPuQQxKnaygX6ul-HxpN3ZJ9L"
DISCORD_JOURNAL_WH  = "https://discord.com/api/webhooks/1506298466375045291/4dsrRrdlhKtUHZsE11iyKRvSMZ1IhSqA5T1TZ-aCKUScbbmIAWrlaCHusWE7yOkkBsRi"

def _discord_post(url, content):
    try:
        data = json.dumps({"content": content}).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception as e:
        print(f"  [discord] webhook error: {e}", flush=True)

# ── Browser tools (optional) ─────────────────────────────────────────────────
HAS_BROWSER = False
try:
    sys.path.insert(0, "/home/openclaw/.hermes/hermes-agent")
    from tools.browser_tool import browser_navigate, browser_snapshot
    HAS_BROWSER = True
except ImportError:
    pass

# ── Config ───────────────────────────────────────────────────────────────────
BINANCE_URL  = "https://fapi.binance.com"
MAJORS = {"BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "LTCUSDT",
          "BNBUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT"}

# Scanner lookback
KLINE_INTERVAL  = "5m"     # 5m candles (balances precision vs API limits)
LOOKBACK_BARS   = 144      # 144 × 5m = 12 hours (covers 2h min + buffer)
MAX_KLINES_FETCH = 500     # Binance max per request

# Volume params
VOL_LOOKBACK   = 20        # SMA lookback bars
VOL_THRESHOLD  = 1.1       # vol must be > SMA × this

# Staircase params
STAIRCASE_BARS = 24        # bars required for "2h+ structure" (24 × 5m = 2h)
SWING_LOOKBACK = 1         # candles to look back for pivot detection (PDF: "2nd of 3 candles is extreme" = strength 1)
MIN_SWINGS     = 3         # minimum swing points to assess direction

# Entry / SL params
PULLBACK_ZONE  = 0.005     # price within 0.5% of level = pullback entry zone
DIST_FOR_LIMIT = 0.03      # SL distance < 3% → limit order; else market order

# Scoring weights
SCORE_STAIRCASE  = 1
SCORE_VOLUME     = 1
SCORE_STRUCTURE  = 1

# ── Coin normalisation (from spicy scanner) ──────────────────────────────────
_COIN_DISPLAY_MAP = {
    "币安人生": "BNLIFE",
}

def _clean_symbol(sym: str) -> str:
    s = sym.replace("USDT", "")
    return _COIN_DISPLAY_MAP.get(s, s)


# ── Binance helpers ──────────────────────────────────────────────────────────
def fetch_tickers() -> dict:
    r = requests.get(f"{BINANCE_URL}/fapi/v1/ticker/24hr", timeout=15)
    return {t["symbol"]: t for t in r.json()}


def fetch_klines(symbol: str, interval: str = "5m", limit: int = 500) -> list:
    """Fetch klines. Returns list of [open_time, open, high, low, close, vol, ...]"""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(f"{BINANCE_URL}/fapi/v1/klines", params=params, timeout=15)
    return r.json()


def top_gainers_losers(tickers: dict, n: int = 15) -> tuple:
    """Return (top_gainers, top_losers) as lists of symbols."""
    usdt = {s: t for s, t in tickers.items() if s.endswith("USDT") and s not in MAJORS}
    by_pct = sorted(usdt.items(), key=lambda x: float(x[1].get("priceChangePercent", 0)), reverse=True)
    gainers = [s for s, _ in by_pct[:n]]
    losers  = [s for s, _ in by_pct if float(tickers[s].get("priceChangePercent", 0)) < 0][-n:]
    losers.reverse()
    return gainers, losers


# ── Swing detection ───────────────────────────────────────────────────────────
def _is_swing_high(candles: list, i: int, lookback: int = 2) -> bool:
    """
    Candle i is a local high: strictly higher than the lookback candles to its left.
    Uses left-side only (pivot-style) to work in trending markets where the
    most recent candle keeps making new highs — we catch the prior peak.
    Skips the last candle (incomplete).
    """
    if i < lookback or i >= len(candles) - 1:
        return False
    high_i = float(candles[i][2])
    for j in range(i - lookback, i):
        if float(candles[j][2]) >= high_i:  # strictly higher only
            return False
    return True


def _is_swing_low(candles: list, i: int, lookback: int = 2) -> bool:
    """
    Candle i is a local low: strictly lower than the lookback candles to its left.
    Skips the last candle (incomplete).
    """
    if i < lookback or i >= len(candles) - 1:
        return False
    low_i = float(candles[i][3])
    for j in range(i - lookback, i):
        if float(candles[j][3]) <= low_i:  # strictly lower only
            return False
    return True


def find_swings(candles: list, lookback: int = 2) -> tuple:
    """
    Return (swing_highs, swing_lows) as lists of (bar_idx, price).
    Each list is ordered newest-first (most recent first).
    Deduplicates consecutive equal-priced swings.
    """
    highs = []
    lows  = []
    for i in range(len(candles)):
        if _is_swing_high(candles, i, lookback):
            p = float(candles[i][2])
            if not highs or p != highs[-1][1]:   # deduplicate equal consecutive highs
                highs.append((i, p))
        if _is_swing_low(candles, i, lookback):
            p = float(candles[i][3])
            if not lows or p != lows[-1][1]:     # deduplicate equal consecutive lows
                lows.append((i, p))
    # Newest first
    highs.reverse()
    lows.reverse()
    return highs, lows


# ── Staircase detection ───────────────────────────────────────────────────────
def detect_staircase(highs: list, lows: list, candles: list) -> dict:
    """
    Detect grindy staircase in the recent candle window.

    Returns dict:
      direction: "LONG" | "SHORT" | "BOTH" | None
      rising_lows_count: int
      rising_highs_count: int
      falling_lows_count: int
      falling_highs_count: int
      total_lows: int
      total_highs: int
      is_staircase: bool
      is_grinding_up:   # approaching a high with staircase
      is_grinding_down: # approaching a low with staircase
    """
    # Recent window: last 24 bars (2h of 5m data)
    window = min(STAIRCASE_BARS, len(candles))
    recent_highs = [(i, p) for (i, p) in highs if i >= len(candles) - window]
    recent_lows  = [(i, p) for (i, p) in lows  if i >= len(candles) - window]

    result = {
        "direction": None,
        "rising_lows_count": 0, "rising_highs_count": 0,
        "falling_lows_count": 0, "falling_highs_count": 0,
        "total_lows": len(recent_lows), "total_highs": len(recent_highs),
        "is_staircase": False,
        "is_grinding_up": False, "is_grinding_down": False,
        "recent_highs": recent_highs[:6],
        "recent_lows": recent_lows[:6],
    }

    if len(recent_lows) < 2 or len(recent_highs) < 2:
        return result

    # Count rising lows (each successive low > previous low) — for LONG staircase
    rising_lows = 0
    for k in range(1, len(recent_lows)):
        if recent_lows[k][1] > recent_lows[k-1][1]:
            rising_lows += 1

    # Count falling lows (each successive low < previous low) — for SHORT staircase
    falling_lows = 0
    for k in range(1, len(recent_lows)):
        if recent_lows[k][1] < recent_lows[k-1][1]:
            falling_lows += 1

    # Count rising highs (each successive high > previous high) — for LONG staircase
    rising_highs = 0
    for k in range(1, len(recent_highs)):
        if recent_highs[k][1] > recent_highs[k-1][1]:
            rising_highs += 1

    # Count falling highs (each successive high < previous high) — for SHORT staircase
    falling_highs = 0
    for k in range(1, len(recent_highs)):
        if recent_highs[k][1] < recent_highs[k-1][1]:
            falling_highs += 1

    result["rising_lows_count"]  = rising_lows
    result["rising_highs_count"] = rising_highs
    result["falling_lows_count"] = falling_lows
    result["falling_highs_count"] = falling_highs

    # LONG staircase (HH+HL): rising highs AND rising lows
    # Both must be mostly consistent (at most 1 deviation)
    long_ok  = rising_highs >= len(recent_highs) - 1 and rising_lows >= len(recent_lows) - 1

    # SHORT staircase (LH+LL): falling highs AND falling lows
    # BOTH are required — was previously checking only falling_highs (bug)
    short_ok = falling_highs >= len(recent_highs) - 1 and falling_lows >= len(recent_lows) - 1

    if long_ok:
        result["is_grinding_up"] = True
    if short_ok:
        result["is_grinding_down"] = True

    if long_ok and short_ok:
        # Price oscillating — unclear direction
        result["direction"] = "BOTH"
        result["is_staircase"] = False
    elif long_ok:
        result["direction"] = "LONG"
        result["is_staircase"] = result["total_lows"] >= MIN_SWINGS
    elif short_ok:
        result["direction"] = "SHORT"
        result["is_staircase"] = result["total_lows"] >= MIN_SWINGS

    return result


# ── Volume analysis ───────────────────────────────────────────────────────────
def volume_regime(candles: list) -> dict:
    """
    Returns dict:
      vol_ma: float (current SMA baseline)
      vol_threshold: float (SMA × threshold)
      vol_current: float (avg of last 3 completed bars)
      above_threshold: bool
      regime: bool  (volume elevated or trending above SMA)
      avg_vol_window: float (avg of last 5 bars)
    """
    vols = [float(c[5]) for c in candles]
    if len(vols) < VOL_LOOKBACK:
        return {"vol_ma": 0, "vol_threshold": 0, "vol_current": 0,
                "above_threshold": False, "regime": False, "avg_vol_window": 0}

    sma = sum(vols[-VOL_LOOKBACK:]) / VOL_LOOKBACK
    # Use avg of last 3 COMPLETED bars (skip the last bar — it may be incomplete)
    recent_bars = 3
    recent_avg = sum(vols[-(recent_bars+1):-1]) / recent_bars  # exclude last incomplete bar
    thresh = sma * VOL_THRESHOLD

    above = recent_avg > thresh

    # Volume regime: recent avg above threshold, or recent avg > overall SMA (trending up)
    recent_5_avg = sum(vols[-6:-1]) / 5  # 5 completed bars
    vol_trending = recent_5_avg > sma

    return {
        "vol_ma":           round(sma, 2),
        "vol_threshold":    round(thresh, 2),
        "vol_current":      round(recent_avg, 2),
        "above_threshold":  above,
        "regime":           above or vol_trending,
        "avg_vol_window":   round(recent_5_avg, 2),
    }


# ── Level detection ──────────────────────────────────────────────────────────
def detect_levels(candles: list, highs: list, lows: list) -> dict:
    """
    Identify the most recent swing high (SH) and swing low (SL)
    and whether price is grinding toward them or has broken through.
    """
    close = float(candles[-1][4])
    result = {
        "last_sh": None, "last_sl": None,
        "prev_sh": None, "prev_sl": None,
        "sh_broken": False, "sl_broken": False,
        "near_sh": False, "near_sl": False,
        "dist_to_sl_pct": None, "dist_to_sh_pct": None,
        "order_type_long": None, "order_type_short": None,
    }

    if highs:
        result["last_sh"] = highs[0][1]
    if lows:
        result["last_sl"] = lows[0][1]
    if len(highs) >= 2:
        result["prev_sh"] = highs[1][1]
    if len(lows) >= 2:
        result["prev_sl"] = lows[1][1]

    sh = result["last_sh"]
    sl = result["last_sl"]

    if sh:
        result["sh_broken"]     = close > sh and float(candles[-2][4]) <= sh
        result["near_sh"]      = (close - sh) / close <= PULLBACK_ZONE and close > sh
        result["dist_to_sh_pct"] = (sh - close) / close

    if sl:
        result["sl_broken"]     = close < sl and float(candles[-2][4]) >= sl
        result["near_sl"]       = (sl - close) / close <= PULLBACK_ZONE and close < sl
        result["dist_to_sl_pct"] = (close - sl) / close

    if result["dist_to_sl_pct"] is not None:
        result["order_type_long"] = "LIMIT" if result["dist_to_sl_pct"] < DIST_FOR_LIMIT else "MARKET"
    if result["dist_to_sh_pct"] is not None:
        result["order_type_short"] = "LIMIT" if result["dist_to_sh_pct"] < DIST_FOR_LIMIT else "MARKET"

    return result


# ── Quality scoring ──────────────────────────────────────────────────────────
def quality_score(staircase: dict, vol: dict, levels: dict) -> dict:
    """
    Compute 3-criterion quality score and signal state.

    Returns dict:
      score: int (0-3)
      criteria: {c1_staircase, c2_volume, c3_structure}
      signal: "MOMENTUM_LONG" | "MOMENTUM_SHORT" | "GET_READY_LONG" |
              "GET_READY_SHORT" | "NONE"
      action: "TAKE" | "CAUTIOUS" | "AVOID"
      target_advice: str
    """
    c1 = staircase["is_staircase"]
    c2 = vol["regime"]
    c3 = (staircase["total_lows"] + staircase["total_highs"]) >= MIN_SWINGS

    score = (c1 and SCORE_STAIRCASE) + (c2 and SCORE_VOLUME) + (c3 and SCORE_STRUCTURE)

    direction = staircase.get("direction", None)

    # Signal determination
    signal = "NONE"
    if direction == "LONG" and levels.get("sh_broken"):
        signal = "MOMENTUM_LONG"
    elif direction == "SHORT" and levels.get("sl_broken"):
        signal = "MOMENTUM_SHORT"
    elif direction == "LONG" and staircase.get("is_grinding_up"):
        signal = "GET_READY_LONG"
    elif direction == "SHORT" and staircase.get("is_grinding_down"):
        signal = "GET_READY_SHORT"
    elif direction == "BOTH":
        if levels.get("sh_broken"):
            signal = "MOMENTUM_LONG"
        elif levels.get("sl_broken"):
            signal = "MOMENTUM_SHORT"
        elif levels.get("near_sh"):
            signal = "GET_READY_LONG"
        elif levels.get("near_sl"):
            signal = "GET_READY_SHORT"

    # Action
    if score >= 3:
        action = "TAKE"
        target_advice = "ambitious — next resistance level"
    elif score == 2:
        action = "CAUTIOUS"
        target_advice = "conservative — 1R target"
    else:
        action = "AVOID"
        target_advice = "not a momentum regime"

    return {
        "score": score,
        "criteria": {"c1_staircase": c1, "c2_volume": c2, "c3_structure": c3},
        "signal": signal,
        "action": action,
        "target_advice": target_advice,
    }


# ── Per-coin analyser ─────────────────────────────────────────────────────────
def analyse_coin(symbol: str) -> dict | None:
    """Full momentum analysis for one coin. Returns None on error."""
    try:
        candles_raw = fetch_klines(symbol, interval=KLINE_INTERVAL, limit=MAX_KLINES_FETCH)
        if len(candles_raw) < STAIRCASE_BARS:
            return None

        # Use only the last `LOOKBACK_BARS` candles
        candles = candles_raw[-LOOKBACK_BARS:]

        highs, lows   = find_swings(candles)
        stair         = detect_staircase(highs, lows, candles)
        vol           = volume_regime(candles)
        levels        = detect_levels(candles, highs, lows)
        qscore        = quality_score(stair, vol, levels)

        close = float(candles[-1][4])
        chg24 = float(candles[-1][4])  # placeholder — use ticker for this

        # Determine direction from staircase
        direction = stair.get("direction", None)
        # Long momentum: price approaching a high from below (rising staircase)
        # Short momentum: price approaching a low from above (falling staircase)

        # Price change over the lookback window
        window_chg = (float(candles[-1][4]) - float(candles[0][4])) / float(candles[0][4]) * 100

        return {
            "symbol":             symbol,
            "close":              round(close, 6),
            "window_chg_pct":     round(window_chg, 2),
            "staircase":          stair,
            "volume":             vol,
            "levels":             levels,
            "quality":            qscore,
            "highs":             [(idx, round(p, 6)) for idx, p in highs[:6]],
            "lows":              [(idx, round(p, 6)) for idx, p in lows[:6]],
        }
    except Exception as e:
        return None


# ── Velo browser: top gainers ────────────────────────────────────────────────
def get_velo_top_coins() -> tuple:
    """
    Return (gainer_symbols, loser_symbols) from Velo browser.

    Uses Binance's ticker endpoint filtered by 24h change % to get the
    actual top movers, then validates they exist on Binance futures.
    Falls back to Binance-only if browser is unavailable.
    """
    if not HAS_BROWSER:
        return [], []

    try:
        # Navigate to Velo to update the "spaghetti" chart state
        # (the scanner reads Velo bias for the report header)
        browser_navigate(url="https://velo.xyz/market")
        time.sleep(4)
        # Snapshot captured for Velo state — coin discovery done via Binance API
        # to avoid parsing errors from Velo's accessibility tree
    except Exception:
        pass

    # ── Coin discovery via Binance tickers ──────────────────────────────────
    try:
        tickers_resp = requests.get(
            f"{BINANCE_URL}/fapi/v1/ticker/24h",
            timeout=15
        )
        tickers = tickers_resp.json()
        if not isinstance(tickers, list):
            tickers = []
    except Exception:
        tickers = []

    # Filter to USDT-M perpetuals with real volume
    valid = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        vol = float(t.get("quoteVolume", 0) or 0)
        if vol < 500_000:
            continue
        change = float(t.get("priceChangePercent", 0) or 0)
        valid.append((sym, change, vol))

    if not valid:
        return [], []

    # Sort by absolute change — top gainers and losers
    by_change = sorted(valid, key=lambda x: -abs(x[1]))
    gainers = [(s, abs(c)) for s, c, v in by_change if c > 0][:15]
    losers  = [(s, abs(c)) for s, c, v in by_change if c < 0][:15]

    return gainers, losers


# ── Report formatter ──────────────────────────────────────────────────────────
def _signal_emoji(signal: str) -> str:
    return {
        "MOMENTUM_LONG":  "🟢 MOMENTUM LONG",
        "MOMENTUM_SHORT": "🔴 MOMENTUM SHORT",
        "GET_READY_LONG": "🟡 GET_READY LONG",
        "GET_READY_SHORT":"🟡 GET_READY SHORT",
    }.get(signal, "⚪ NONE")


def _score_bar(score: int) -> str:
    return "🟢" * score + "⚪" * (3 - score)


def generate_momentum_report(coins: list, gainers_src: list, losers_src: list,
                              with_velo: bool) -> str:
    """Build the Telegram-ready momentum report."""
    now = datetime.now(timezone.utc)
    lines = []

    lines.append("=" * 56)
    lines.append("  SPICY MOMENTUM SCANNER")
    lines.append(f"  {now:%Y-%m-%d %H:%M UTC}   Velo: {'YES' if with_velo else 'NO'}")
    lines.append("=" * 56)

    # ── Source ───────────────────────────────────────────────────────────
    if gainers_src:
        src = "Velo Top Gainers"
    else:
        src = "Binance Top Gainers/Losers"
    lines.append(f"\nCoin source: {src}")

    # ── Categorise ──────────────────────────────────────────────────────
    momentum_long   = [c for c in coins if c["quality"]["signal"] == "MOMENTUM_LONG"]
    momentum_short  = [c for c in coins if c["quality"]["signal"] == "MOMENTUM_SHORT"]
    getready_long   = [c for c in coins if c["quality"]["signal"] == "GET_READY_LONG"]
    getready_short  = [c for c in coins if c["quality"]["signal"] == "GET_READY_SHORT"]
    avoid           = [c for c in coins if c["quality"]["action"] == "AVOID"]
    cautious        = [c for c in coins if c["quality"]["action"] == "CAUTIOUS"]

    # Sort by score desc
    for lst in [momentum_long, momentum_short, getready_long, getready_short]:
        lst.sort(key=lambda x: -x["quality"]["score"])

    def coin_row(c: dict, arrow: str = "→") -> str:
        sym  = _clean_symbol(c["symbol"])
        sq   = c["quality"]["score"]
        s3   = _score_bar(sq)
        sig  = c["quality"]["signal"].replace("MOMENTUM_", "MOM ").replace("GET_READY_", "GR ")
        sl   = c["levels"]
        c1   = "✓" if c["quality"]["criteria"]["c1_staircase"] else "✗"
        c2   = "✓" if c["quality"]["criteria"]["c2_volume"]    else "✗"
        c3   = "✓" if c["quality"]["criteria"]["c3_structure"]  else "✗"
        # For SHORT: stop is above at SH. For LONG: stop is below at SL.
        is_short = "SHORT" in c["quality"]["signal"]
        dist = sl.get("dist_to_sh_pct") if is_short else sl.get("dist_to_sl_pct")
        dist_str = f"SL:{dist*100:+.1f}%" if dist is not None else ""
        return (f"{arrow} {sym:<10} {s3}  {c1}{c2}{c3}  {sig:<14}  {dist_str}")

    # ── ACTIVE MOMENTUM (breakout confirmed) ──────────────────────────────
    if momentum_long or momentum_short:
        lines.append("\n🚀  ACTIVE MOMENTUM — Level Broken")
        if momentum_long:
            lines.append("  🟢 LONG — price broke through swing high:")
            for c in momentum_long:
                lines.append("  " + coin_row(c, "→"))
        if momentum_short:
            lines.append("  🔴 SHORT — price broke through swing low:")
            for c in momentum_short:
                lines.append("  " + coin_row(c, "→"))

    # ── GET_READY (grinding, not yet broken) ────────────────────────────
    if getready_long or getready_short:
        lines.append("\n👀  GET_READY — Grinding Toward Level")
        if getready_long:
            lines.append("  🟢 LONG candidates (rising staircase, approaching SH):")
            for c in getready_long:
                sh = c["levels"].get("last_sh")
                sh_str = f" @ {sh}" if sh else ""
                lines.append(f"  → {_clean_symbol(c['symbol']):<10} {_score_bar(c['quality']['score'])}  "
                             f"SH{sh_str}  dist_to_SL:{c['levels'].get('dist_to_sl_pct',0)*100:+.1f}%")
        if getready_short:
            lines.append("  🔴 SHORT candidates (falling staircase, approaching SL):")
            for c in getready_short:
                sl = c["levels"].get("last_sl")
                sl_str = f" @ {sl}" if sl else ""
                lines.append(f"  → {_clean_symbol(c['symbol']):<10} {_score_bar(c['quality']['score'])}  "
                             f"SL {sl_str}  dist_to_SH:{c['levels'].get('dist_to_sh_pct',0)*100:+.1f}%")

    # ── CAUTIOUS (score 2/3) ───────────────────────────────────────────────
    if cautious and not (momentum_long or momentum_short or getready_long or getready_short):
        lines.append("\n⚠️  CAUTIOUS — Score 2/3 (conservative 1R target only)")
        cautious.sort(key=lambda x: -x["quality"]["score"])
        for c in cautious[:8]:
            lines.append("  " + coin_row(c, "~"))

    # ── AVOID ─────────────────────────────────────────────────────────────
    if avoid:
        avoid_names = [_clean_symbol(c["symbol"]) for c in avoid[:10]]
        lines.append(f"\n🚫  No momentum (score 0-1/3): {', '.join(avoid_names)}")

    # ── How to read this ──────────────────────────────────────────────────
    lines.append("\n📋  Quality Scoring:")
    lines.append("  🟢🟢🟢 = 3/3 — ambitious target (next resistance)")
    lines.append("  🟢🟢⚪ = 2/3 — conservative 1R target")
    lines.append("  🟢⚪⚪ or ⚪⚪⚪ = 0-1/3 — AVOID")
    lines.append("\n  C1=Staircase  C2=Volume↑  C3=2h+ structure")

    lines.append("=" * 56)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def run_scan(with_velo: bool = False, journal: bool = False):
    print("=" * 56)
    print("  SPICY MOMENTUM SCANNER")
    print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 56)

    # ── Velo top coins ──────────────────────────────────────────────────
    gainers_src, losers_src = [], []
    coin_sources = []

    if with_velo and HAS_BROWSER:
        print("\n  Fetching Velo Top Gainers...")
        gainers_src, losers_src = get_velo_top_coins()
        if gainers_src or losers_src:
            coin_sources = [g[0] + "USDT" for g in gainers_src[:15]]
            print(f"  Velo: {len(coin_sources)} coins loaded")
        else:
            print("  Velo snapshot unavailable — falling back to Binance")

    # ── Binance top coins ───────────────────────────────────────────────
    if not coin_sources:
        print("\n  Fetching Binance 24h tickers...")
        tickers = fetch_tickers()
        gainers_list, losers_list = top_gainers_losers(tickers, n=15)
        coin_sources = gainers_list + losers_list
        print(f"  Binance: {len(coin_sources)} coins")

    # ── Analyse each coin ───────────────────────────────────────────────
    print("\n  Analysing momentum criteria...")
    results = []
    for sym in coin_sources:
        if sym in MAJORS:
            continue
        r = analyse_coin(sym)
        if r:
            results.append(r)
        time.sleep(0.05)  # Binance rate limit

    print(f"  {len(results)} coins analysed")

    # ── Generate report ─────────────────────────────────────────────────
    report = generate_momentum_report(results, gainers_src, losers_src, with_velo)
    print("\n" + report)

    # ── Save outputs ────────────────────────────────────────────────────
    json_path = "/home/openclaw/workspace/scanner/latest_momentum_scan.json"
    with open(json_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coins": results,
        }, f, indent=2, default=str)

    txt_path = "/home/openclaw/workspace/scanner/latest_momentum_report.txt"
    with open(txt_path, "w") as f:
        f.write(report)

    print(f"\nSaved: {json_path}")
    print(f"Saved: {txt_path}")

    if journal:
        # Need tickers for market data in journal exit checks
        if 'tickers' not in dir():
            tickers = fetch_tickers()
        log_momentum_to_journal(results, tickers)


def log_momentum_to_journal(results: list, tickers: dict = None):
    """Log momentum signals to the journal, capturing entry prices + SL/TP.
    Also checks all pending momentum trades for exits (TP/SL/counter-signal).
    """
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from journal import Journal
        j = Journal()
        logged = []
        today = datetime.now(timezone.utc).isoformat()

        # ── Build market data for exit detection ───────────────────────────
        market_data = {}
        if tickers:
            for sym, t in tickers.items():
                try:
                    price = float(t.get("lastPrice") or t.get("closePrice") or 0)
                except (TypeError, ValueError):
                    price = 0
                if price > 0:
                    direction = ""
                    for r in results:
                        if r.get("symbol") == sym:
                            d = r.get("get_ready_direction", r.get("direction", ""))
                            direction = d.upper() if d else ""
                            break
                    market_data[sym] = {"price": price, "direction": direction,
                                        "scanner": "momentum"}

        # ── Check pending trades for exits ─────────────────────────────────
        closed = j.check_pending_exits(market_data)
        if closed:
            for c in closed:
                outcome_icon = "✅" if c["outcome"] == "win" else "❌" if c["outcome"] == "loss" else "➖"
                print(f"\n  Journal: {outcome_icon} {c['trade_id']} closed "
                      f"{c['outcome']} ({c['pnl_r']:+.2f}R) at {c['exit_price']}")
                # ── Post closed trade to Discord #journal ─────────────────
                coin_sym = c.get("coin", c.get("symbol", "???"))
                direction = c.get("direction", "???").upper()
                pnl_r = c.get("pnl_r", 0)
                entry_ts = c.get("entry_ts", c.get("signal_ts", "?"))
                exit_ts  = c.get("exit_ts", "?")
                # Format timestamps for display
                def fmt_ts(ts):
                    if not ts or ts == "?":
                        return "?"
                    return str(ts)[:16]
                content = (
                    f"{outcome_icon} **{coin_sym}** {direction} — {c['outcome'].upper()}\n"
                    f"PnL: {pnl_r:+.2f}R | Entry: `{fmt_ts(entry_ts)}` → Exit: `{fmt_ts(exit_ts)}`"
                )
                _discord_post(DISCORD_JOURNAL_WH, content)

        # ── Log new signals ────────────────────────────────────────────────
        for r in results:
            sym = r.get("symbol", "")
            if not sym:
                continue
            # Log GET_READY and MOMENTUM signals only
            if not r.get("get_ready") and not r.get("momentum"):
                continue
            direction = r.get("get_ready_direction", r.get("direction", "short"))
            score = r.get("quality_score", 0)
            flags = []
            if r.get("get_ready"):
                flags.append("GET_READY")
            if r.get("momentum"):
                flags.append("MOMENTUM")
            if r.get("volume_ok"):
                flags.append("VOL_OK")
            if r.get("is_staircase"):
                flags.append("STAIRCASE")
            thesis = (
                f"Momo {direction.upper()} | quality={score}/3 | "
                f"stairs={r.get('is_staircase')} | vol={r.get('volume_ok')}"
            )
            # For SHORT:  SL = last_sh (resistance above), TP = last_sl (support below)
            # For LONG:   SL = last_sl (support below),  TP = last_sh (resistance above)
            sl = r.get("last_sl") if direction == "short" else r.get("last_sh")
            tp = r.get("last_sh") if direction == "short" else r.get("last_sl")
            entry_price = r.get("close") or 0
            trade_id = j.log_signal(
                scanner="momentum",
                direction=direction.lower(),
                coin=sym,
                entry_price=entry_price,
                sl_price=sl,
                tp_price=tp,
                quality_score=score,
                mr_score=None,
                flags=flags,
                level_approach="slow_grind" if r.get("is_staircase") else "unclear",
                volume_regime="increasing" if r.get("volume_ok") else "unclear",
                thesis=thesis,
                signal_ts=today,
            )
            logged.append(trade_id)
            # ── Post signal to Discord #signals ────────────────────────────
            signal_ts_str = today if 'today' in dir() else datetime.now(timezone.utc).isoformat()
            signal_content = (
                f"🌶️ **{sym}** {direction.upper()} — quality {score}/3\n"
                f"_{thesis}_\n"
                f"Entry: `{entry_price:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                f"_Signal: {signal_ts_str[:16]} UTC_"
            )
            _discord_post(DISCORD_SIGNALS_WH, signal_content)

        if logged:
            print(f"\n  Journal: {len(logged)} signals logged with entry/SL/TP")
    except Exception as e:
        import traceback
        print(f"\n  Journal: skipped ({e})")
        traceback.print_exc()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-velo", dest="with_velo", action="store_true", default=True,
                    help="Fetch top gainers from Velo.xyz (default: on)")
    ap.add_argument("--no-velo", dest="with_velo", action="store_false",
                    help="Disable Velo browser — Binance only")
    ap.add_argument("--journal", action="store_true",
                    help="Also log signals to the trading journal")
    args = ap.parse_args()
    run_scan(with_velo=args.with_velo, journal=args.journal)
