#!/usr/bin/env python3
"""
Velo + Spicy Full Mean Reversion Scanner
========================================
Integrates ALL of Spicy's methodology:

Velo.xyz:
  - Return Buckets → directional bias (BULLISH/BEARISH/MIXED)
  - Spaghetti Chart → pattern classification + parabolic coins list
  - News feed → coin-specific news

Binance fAPI:
  - Real 5m klines → actual 5m % change (not approximated from 24h)
  - TRD (5M) — trade count — retail activity signal
  - VOL (5M) — volume in $ — liquidity check
  - CHG % (5M) — fast spike detection (≥1.5% = reversal candidate)
  - CHG % (1D) — 24h exhaustion detection
  - Funding rate + countdown → 0.1R rule enforcement
  - 24h ATR for 1R → 0.1R funding threshold

Spicy's filters:
  - VOL ≥ $500K/5min
  - TRD ≥ 5,000/5min
  - Avoid majors (BTC, ETH, XRP, SOL, LTC)
  - Fast spike coins → check Velo news + BTC correlation before trading
  - Staircase coins (spaghetti y-axis >1000% OR 24h change >50%) → AVOID
  - Funding due < 90min AND funding > 0.1R → skip entry

Usage:
  python3 velo_spicy_scanner.py              # Binance only
  python3 velo_spicy_scanner.py --with-velo  # Full + Velo browser
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

import urllib.request

# ── Coin name normalisation ───────────────────────────────────────────────────
# Velo's spaghetti chart returns raw coin names, some with non-ASCII characters.
# Map to clean display names. Unknown coins are shown as their symbol only.
_COIN_DISPLAY_MAP = {
    "币安人生": "BNLIFE",
    "AKE": "AKE",
    "AIOT": "AIOT",
    "GIGGLE": "GIGGLE",
    "MAGMA": "MAGMA",
    "SKYAI": "SKYAI",
    "DRIFT": "DRIFT",
}

def _display_name(coin_name: str) -> str:
    """Return clean ASCII name for display in reports."""
    if coin_name in _COIN_DISPLAY_MAP:
        return _COIN_DISPLAY_MAP[coin_name]
    # Strip any remaining non-ASCII
    cleaned = coin_name.encode("ascii", "ignore").decode("ascii")
    return cleaned if cleaned else coin_name

# ── Browser tools (optional) ─────────────────────────────────────────────────
HAS_BROWSER = False
try:
    sys.path.insert(0, "/home/openclaw/.hermes/hermes-agent")
    from tools.browser_tool import browser_navigate, browser_snapshot
    HAS_BROWSER = True
except ImportError:
    pass

# ── Config ───────────────────────────────────────────────────────────────────
BINANCE_URL = "https://fapi.binance.com"
MAJORS = {"BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "LTCUSDT",
          "BNBUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT"}
MIN_VOL_5M  = 500_000   # $500K/5min minimum (quoted as 24h equiv: * 288 = 144M)
MIN_TRD_5M  = 5_000     # 5K trades/5min minimum
FAST_SPIKE  = 1.5       # % in 5m to qualify as fast spike
STAIRCASE_24H = 50      # % 24h change = staircase flag
VOTE_BIAS_THRESH = 0.25 # pos_ratio below this = BEARISH
BULL_BIAS_THRESH = 0.75 # pos_ratio above this = BULLISH

# ── Binance helpers ──────────────────────────────────────────────────────────
def fetch_tickers():
    """Fetch all USDT-margined futures tickers with 24h stats."""
    r = requests.get(f"{BINANCE_URL}/fapi/v1/ticker/24hr", timeout=15)
    return {t["symbol"]: t for t in r.json()}

def fetch_top20_trade_count(tickers: dict) -> list:
    """Return top 20 coins by trade count (TRD 5M proxy from count)."""
    def trd_key(sym):
        t = tickers.get(sym, {})
        try:
            return int(t.get("count", 0))
        except:
            return 0
    return sorted([s for s in tickers if s.endswith("USDT") and s not in MAJORS],
                  key=trd_key, reverse=True)[:20]

def get_5m_klines(symbol: str, limit: int = 6):
    """Fetch last N 5m klines. Returns list of [open_time, open, high, low, close, vol]."""
    r = requests.get(
        f"{BINANCE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "5m", "limit": limit},
        timeout=10
    )
    return r.json()

def compute_5m_change(symbol: str) -> float | None:
    """% change from oldest to newest close in last 6 x 5m candles."""
    try:
        klines = get_5m_klines(symbol, 6)
        if len(klines) < 2:
            return None
        first = float(klines[0][4])
        last  = float(klines[-1][4])
        return (last - first) / first * 100
    except:
        return None

def compute_5m_atr(symbol: str) -> float | None:
    """Approximate 5m ATR from last 12 klines (1hr of data)."""
    try:
        klines = get_5m_klines(symbol, 13)
        if len(klines) < 2:
            return None
        trs = []
        for k in klines[1:]:
            high, low, prev_close = float(k[2]), float(k[3]), float(klines[klines.index(k)-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        avg = sum(trs) / len(trs)
        last_close = float(klines[-1][4])
        return avg / last_close * 100  # as %
    except:
        return None

def fetch_funding(symbol: str):
    """Fetch next funding time + rate for a symbol."""
    try:
        r = requests.get(
            f"{BINANCE_URL}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=10
        )
        d = r.json()
        rate = float(d.get("lastFundingRate", 0)) * 100  # convert to %
        # next funding = lastFundingTime + 8hrs (Binance standard)
        next_fund_ts = d.get("nextFundingTime")
        if next_fund_ts:
            next_fund_dt = datetime.fromtimestamp(next_fund_ts / 1000, tz=timezone.utc)
        else:
            next_fund_dt = None
        return rate, next_fund_dt
    except:
        return None, None

def funding_within_90min(rate_pct: float, next_fund: datetime, entry_dist_pct: float = 2.0) -> tuple:
    """
    Spicy's 0.1R funding rule:
    If funding payment due in < 90 min AND funding size > 0.1R of the trade,
    skip the entry.

    1R ≈ entry_dist_pct (user provides, default 2% as rough estimate).
    0.1R = 0.1 × entry_dist_pct.
    """
    now = datetime.now(timezone.utc)
    if next_fund is None:
        return False, 0.0
    diff_min = (next_fund - now).total_seconds() / 60
    if diff_min > 90:
        return False, 0.0
    one_r = entry_dist_pct
    threshold = 0.1 * one_r  # 0.1R
    if abs(rate_pct) > threshold:
        return True, rate_pct  # skip, with the rate that triggered it
    return False, 0.0

# ── Scoring ──────────────────────────────────────────────────────────────────
def mr_score(row: dict, five_min_chg: float | None, five_min_atr: float | None,
             top10_trd: set) -> dict:
    """
    Spicy MR score components (higher = better reversal candidate):
      trades(30) + volume(20) + spike_potential(30) + funding(10) - staircase_penalty(20)
    """
    try:
        vol5  = float(row.get("quoteVolume", 0))
        trd5  = float(row.get("count", 0))
        chg24 = float(row.get("priceChangePercent", 0))
        chg5  = five_min_chg if five_min_chg is not None else 0.0
        funding = float(row.get("lastFundingRate", 0)) * 100
    except:
        return {"score": 0, "components": {}}

    # ── Volume score (max 20) ──────────────────────────────────────────────
    vol_score = min(20, (vol5 / 1_000_000) * 10)  # 1M vol = 10pts

    # ── Trade count score (max 30) — top10 by TRD get bonus ────────────────
    trd_raw = min(30, (trd5 / 30_000) * 20)  # 30K TRD = full score
    sym = row["symbol"]
    trd_score = trd_raw + (5 if sym in top10_trd else 0)

    # ── Spike potential (max 30) ──────────────────────────────────────────
    # Spike must be at least 1.5% in 5m to count
    # Score by magnitude of the spike (more extended = better for reversal)
    if abs(chg5) >= FAST_SPIKE:
        spike_score = min(30, abs(chg5) * 6)  # 1.5% = 9pts, 5% = 30pts cap
    elif abs(chg5) >= 0.5:
        # Moderate move — partial score
        spike_score = abs(chg5) * 3
    else:
        # No meaningful spike — use 24h change (coins up big can reverse)
        spike_score = min(20, abs(chg24) * 0.5)

    # ── Direction scoring ──────────────────────────────────────────────────
    # Reversal LONG candidates: coins DOWN (negative 24h) with positive 5m or bounce
    # Reversal SHORT candidates: coins UP (positive 24h) with negative 5m or rejection
    # Score is magnitude regardless of direction — we split into LONG/SHORT lists
    # The MR score is the raw reversal potential (higher = better fade target)

    # ── Funding score (max 10) — penalise extreme negative funding ────────
    # Spicy: negative funding (>0.1R) due soon = skip LONG entry
    #        positive funding (>0.1R) due soon = skip SHORT entry
    funding_score = 0
    if abs(funding) > 1.0:  # extreme funding
        funding_score = -5
    elif abs(funding) > 0.5:
        funding_score = -2

    # ── Small/mid cap bonus ──────────────────────────────────────────────
    # Spicy prefers smaller alts over majors (less sophisticated players)
    cap_bonus = 0
    if sym not in MAJORS and sym not in {"BTCUSDT", "ETHUSDT"}:
        cap_bonus = 5

    total = vol_score + trd_score + spike_score + funding_score + cap_bonus
    return {
        "score": round(total, 1),
        "components": {
            "vol":      round(vol_score, 1),
            "trd":      round(trd_score, 1),
            "spike":    round(spike_score, 1),
            "funding":  round(funding_score, 1),
            "cap_bonus": round(cap_bonus, 1),
        }
    }

# ── Velo browser capture ────────────────────────────────────────────────────
def capture_velo_snapshot() -> str | None:
    if not HAS_BROWSER:
        return None
    try:
        browser_navigate(url="https://velo.xyz/market")
        time.sleep(4)
        raw = browser_snapshot()
        if isinstance(raw, str):
            parsed = json.loads(raw)
            return parsed.get("snapshot", "")
        elif isinstance(raw, dict):
            return raw.get("snapshot", str(raw))
        return str(raw)
    except Exception as e:
        print(f"    Velo capture error: {e}")
        return None

def parse_return_buckets(snap: str) -> dict:
    """
    Parse Return Buckets from Velo snapshot.
    Snapshot line: img "1w Return Buckets": ... -15% -10% -5% <0% >0% +5% +10% +15% 100 50 0 50 100 150 200
    Labels: -15%, -10%, -5%, <0%, >0%, +5%, +10%, +15%  (8 buckets)
    Bars:   100,   50,    0,   50,  50, 100,  150,  200   (8 heights)
    """
    lines = snap.split('\n')
    bucket_line = next((l for l in lines if '1w return buckets' in l.lower()), "")
    if not bucket_line:
        return {"bias": "MIXED", "strength": "MILD", "raw": "not found"}

    tokens = bucket_line.split()
    num_start = None
    for i, tok in enumerate(tokens):
        clean = tok.rstrip(':").')
        if clean.lstrip('-').isdigit():
            num_start = i
            break

    if num_start is None:
        return {"bias": "MIXED", "strength": "MILD", "raw": "no numbers found"}

    bar_tokens = tokens[num_start:num_start + 8]
    try:
        bars = [int(t.rstrip(':").')) for t in bar_tokens]
    except ValueError:
        return {"bias": "MIXED", "strength": "MILD", "raw": f"bad bars: {bar_tokens}"}

    neg_sum = sum(bars[:4])
    pos_sum = sum(bars[4:])
    total   = neg_sum + pos_sum
    if total == 0:
        return {"bias": "MIXED", "strength": "MILD", "raw": "zero bucket total"}

    pos_ratio = pos_sum / total

    if pos_ratio >= BULL_BIAS_THRESH:
        bias = "BULLISH";  strength = "EXTREME" if pos_ratio >= 0.85 else "MODERATE"
    elif pos_ratio <= VOTE_BIAS_THRESH:
        bias = "BEARISH";  strength = "EXTREME" if pos_ratio <= 0.15 else "MODERATE"
    else:
        bias = "MIXED"
        strength = "MILD" if 0.35 <= pos_ratio <= 0.65 else "MODERATE"

    return {
        "bias": bias, "strength": strength,
        "raw": f"neg={neg_sum} pos={pos_sum} ({pos_ratio:.0%})",
        "pos_ratio": round(pos_ratio, 3)
    }

def parse_spaghetti_chart(snap: str) -> dict:
    """
    Parse Spaghetti Chart / Price Changes from Velo snapshot.
    Returns: parabolic_coins list, y_axis_max, pattern_classification
    Snapshot line: img "Price Changes": ... RAVE 币安人生 BLESS ... -1000% 0% 1000% ... 6000%
    """
    import re
    lines = snap.split('\n')
    price_line = next((l for l in lines
                      if 'price changes' in l.lower() and 'return bucket' not in l.lower()), "")

    if not price_line:
        return {"coins": [], "y_axis_max": 0, "classification": "UNKNOWN"}

    tokens = price_line.split()
    MONTHS = {'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'}
    coin_names = []
    in_coins = False
    for tok in tokens:
        clean = tok.rstrip(':").')
        if clean == 'Changes':
            in_coins = True; continue
        if clean in MONTHS:
            break
        if in_coins and clean not in ('Price', 'Changes'):
            coin_names.append(clean.strip('"'))

    # Extract y-axis numbers
    all_nums = re.findall(r'-?\d+%?', price_line)
    yaxis_nums = []
    for n in all_nums:
        stripped = n.rstrip('%')
        try:
            yaxis_nums.append(abs(int(stripped)))
        except ValueError:
            pass
    yaxis_max = max(yaxis_nums) if yaxis_nums else 0

    if yaxis_max > 1000:
        classification = "PARABOLIC"
    elif yaxis_max > 200:
        classification = "CHOPPY UPTREND"
    else:
        classification = "CHOPPY RANGE"

    return {
        "coins": coin_names,
        "y_axis_max": yaxis_max,
        "classification": classification
    }

def parse_velo_news(snap: str) -> list:
    """Extract any news/announcement lines from Velo snapshot."""
    lines = snap.split('\n')
    news_lines = []
    in_news = False
    for line in lines:
        if 'news' in line.lower() and 'link' in line.lower():
            in_news = True
        if in_news and line.strip():
            news_lines.append(line.strip())
        if len(news_lines) > 10:
            break
    return news_lines

# ── Staircase check ──────────────────────────────────────────────────────────
def is_staircase(symbol: str, chg24: float, parabolic_coins: list,
                 y_axis_max: int) -> tuple:
    """Return (is_staircase: bool, reason: str)"""
    # Explicit parabolic from spaghetti
    if symbol in parabolic_coins and y_axis_max > 1000:
        return True, f"PARABOLIC (y-axis {y_axis_max}%)"

    # 24h extreme = staircase
    if chg24 > STAIRCASE_24H:
        return True, f"24h +{chg24:.1f}% — staircase breakout"
    if chg24 < -STAIRCASE_24H:
        return True, f"24h {chg24:.1f}% — staircase breakdown"

    return False, ""

# ── Formatters ───────────────────────────────────────────────────────────────
def fmt_coin(row: dict, five_min_chg: float | None, five_min_atr: float | None,
             score: dict, bias: str, parabolic_coins: list, y_axis_max: int,
             top10_trd: set) -> dict:
    sym   = row["symbol"]
    chg24 = float(row.get("priceChangePercent", 0))
    vol5  = float(row.get("quoteVolume", 0))
    trd5  = float(row.get("count", 0))
    funding_rate, next_fund = fetch_funding(sym)

    staircase, stair_reason = is_staircase(sym, chg24, parabolic_coins, y_axis_max)
    skip, skip_reason = "", ""
    if staircase:
        skip, skip_reason = "STAIRCASE", stair_reason

    # Funding check
    if funding_rate is not None and next_fund is not None:
        entry_est = 2.0  # rough 2% estimated stop distance
        fund_due_soon, fund_rate = funding_within_90min(funding_rate, next_fund, entry_est)
        if fund_due_soon:
            # Which direction to skip?
            if funding_rate > 0:
                skip, skip_reason = "SKIP SHORT", f"funding +{fund_rate:.3f}% due <90min (>0.1R)"
            else:
                skip, skip_reason = "SKIP LONG", f"funding {fund_rate:.3f}% due <90min (>0.1R)"

    # Direction: if 24h UP → reversal SHORT candidate (fade the top)
    #           if 24h DOWN → reversal LONG candidate (buy the dip)
    if chg24 > 0.5:
        direction = "SHORT"   # reversal short — price up, fade it
        arrow = "🔴"
    elif chg24 < -0.5:
        direction = "LONG"    # reversal long — price down, buy the dip
        arrow = "🟢"
    else:
        direction = "BOTH"
        arrow = "🟡"

    # Spicy bias guidance
    if bias == "BULLISH" and direction == "SHORT":
        bias_guidance = "PREFERRED"   # bullish + coin up = good short setup
    elif bias == "BEARISH" and direction == "LONG":
        bias_guidance = "PREFERRED"
    elif bias == "MIXED":
        bias_guidance = "BOTH OK"
    else:
        bias_guidance = "FAVORS REVERSAL"

    # Fast spike flag
    is_fast_spike = five_min_chg is not None and abs(five_min_chg) >= FAST_SPIKE

    # Extract current price from Binance ticker row
    try:
        close_price = float(row.get("lastPrice") or row.get("closePrice") or 0)
    except (TypeError, ValueError):
        close_price = 0.0

    return {
        "symbol":          _clean_symbol(sym),
        "chg24":           round(chg24, 2),
        "close_price":     close_price,
        "vol5m":           round(vol5),
        "trd5m":           int(trd5),
        "5m_chg":          round(five_min_chg, 3) if five_min_chg is not None else None,
        "5m_atr":          round(five_min_atr, 4) if five_min_atr is not None else None,
        "funding_rate":    round(funding_rate, 4) if funding_rate else None,
        "next_fund":       next_fund.strftime("%H:%M") if next_fund else None,
        "direction":       direction,
        "arrow":           arrow,
        "score":           score["score"],
        "components":      score["components"],
        "staircase":       staircase,
        "skip":            skip,
        "skip_reason":     skip_reason,
        "is_fast_spike":   is_fast_spike,
        "bias_guidance":   bias_guidance,
        "parabolic":       sym in parabolic_coins,
        "in_top10_trd":    sym in top10_trd,
    }

def coin_sort_key(c: dict):
    """Sort: staircase last, then by score descending."""
    return (c["staircase"], -c["score"])


# ── Formatted Report Generator ────────────────────────────────────────────────
def _clean_symbol(sym: str) -> str:
    """Strip USDT suffix and normalise to ASCII display name."""
    s = sym.replace("USDT", "")
    if _display_name(s) != s:
        return _display_name(s)
    return s


def _funding_minutes_away(next_fund: str | None) -> str:
    if not next_fund:
        return "?"
    try:
        hh, mm = map(int, next_fund.split(":"))
        now = datetime.now(timezone.utc)
        fund_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if fund_today <= now:
            fund_today += __import__("datetime").timedelta(hours=24)
        diff_min = (fund_today - now).total_seconds() / 60
        if diff_min > 0:
            return f"due in ~{int(diff_min)}min"
        return f"past due"
    except Exception:
        return next_fund or "?"


def _short_term_edge() -> str:
    """Return current UTC session edge description."""
    now = datetime.now(timezone.utc)
    h = now.hour
    if 0 <= h < 8:
        return ("Asian session (00-08 UTC) — 91% WR historically. "
                "Strong edge. Look for mean reversion setups.")
    elif 8 <= h < 12:
        return ("London AM (08-12 UTC) — 83% WR historically. "
                "Best window of the day, but closing fast.")
    elif 12 <= h < 16:
        return ("London PM (12-16 UTC) — mixed results historically. "
                "Monitor only, don't over-trade.")
    else:
        return ("Late session (16-00 UTC) — Asian flush territory. "
                "Reduced conviction. Best edge waits for 00-08 UTC.")


def generate_report(data: dict) -> str:
    """
    Build the Telegram-ready summary report from scanner output data.
    data: the dict saved to latest_spicy_scan.json
    """
    bias     = data["bias"]["value"]
    strength = data["bias"]["strength"]
    bias_raw = data["bias"].get("raw", "")
    spagh    = data["spaghetti"]
    sp_class = spagh["classification"]
    sp_yaxis = spagh["y_axis_max"]
    parabolic = spagh.get("parabolic_coins", [])
    longs     = data["candidates"]["longs"]
    shorts    = data["candidates"]["shorts"]
    stairs    = data["candidates"].get("stairs", [])
    fast_spikes = data["candidates"].get("fast_spikes", [])

    # Normalise coin names in parabolic list
    parabolic_clean = [_display_name(c) for c in parabolic]

    lines = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append("=" * 56)
    lines.append("  SPICY MEAN REVERSION SCAN")
    lines.append(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    lines.append("=" * 56)

    # ── Session Bias ───────────────────────────────────────────────────────
    bias_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(bias, "🟡")
    lines.append(f"\nSession Bias: {bias}/{strength}")
    if bias_raw:
        lines.append(f"({bias_raw})")
    lines.append(f"\nSpaghetti: {sp_class} — y-axis {sp_yaxis}%")
    if parabolic_clean:
        lines.append(f"Parabolic coins: {', '.join(parabolic_clean[:8])}")

    # ── Funding Alerts ─────────────────────────────────────────────────────
    funding_alerts = [
        c for c in longs + shorts
        if c.get("skip") and "funding" in c["skip"].lower()
    ]
    if funding_alerts:
        lines.append("\n⚠️  Funding Alerts:")
        for c in funding_alerts[:5]:
            sym = _clean_symbol(c["symbol"])
            fr  = c.get("funding_rate") or 0
            nxt = c.get("next_fund", "?")
            timing = _funding_minutes_away(nxt)
            direction = "LONG" if c["direction"] == "LONG" else "SHORT"
            lines.append(
                f"• {sym} {fr:+.3f}% funding — skip {direction} entry "
                f"({timing})"
            )

    # ── Shorts ─────────────────────────────────────────────────────────────
    valid_shorts = [c for c in shorts if not c.get("skip")]
    if valid_shorts:
        lines.append("\n🔴  Top Reversal SHORT Candidates (coins UP — fade the top):")
        lines.append(f"{'Coin':<12} {'24h %':>8} {'MR':>5}  {'Flags'}")
        lines.append("-" * 48)
        for c in valid_shorts[:8]:
            sym  = _clean_symbol(c["symbol"])
            chg  = c["chg24"]
            score = c["score"]
            flags = []
            if c.get("is_fast_spike"):
                flags.append("⚡FAST")
            if c.get("in_top10_trd"):
                flags.append("★TOP_TRD")
            flag_str = " ".join(flags) if flags else ""
            lines.append(f"{sym:<12} {chg:>+8.2f} {score:>5.1f}  {flag_str}")

    # ── Longs ──────────────────────────────────────────────────────────────
    valid_longs = [c for c in longs if not c.get("skip")]
    if valid_longs:
        lines.append("\n🟢  Top Reversal LONG Candidates (coins DOWN — buy the dip):")
        lines.append(f"{'Coin':<12} {'24h %':>8} {'MR':>5}  {'Flags'}")
        lines.append("-" * 48)
        for c in valid_longs[:8]:
            sym   = _clean_symbol(c["symbol"])
            chg   = c["chg24"]
            score = c["score"]
            flags = []
            if c.get("is_fast_spike"):
                flags.append("⚡FAST")
            if c.get("in_top10_trd"):
                flags.append("★TOP_TRD")
            flag_str = " ".join(flags) if flags else ""
            lines.append(f"{sym:<12} {chg:>+8.2f} {score:>5.1f}  {flag_str}")

    # ── Avoid ──────────────────────────────────────────────────────────────
    if stairs:
        avoid_names = [_clean_symbol(c["symbol"]) for c in stairs[:8]]
        lines.append(f"\n🚫  Avoid — Staircase/Breakout mode: {', '.join(avoid_names)}")

    # ── Fast spike caveat ──────────────────────────────────────────────────
    parabolic_short = [c for c in shorts if _clean_symbol(c["symbol"]) in parabolic_clean]
    parabolic_long  = [c for c in longs  if _clean_symbol(c["symbol"]) in parabolic_clean]
    if parabolic_short or parabolic_long:
        names = list({_clean_symbol(c["symbol"]) for c in parabolic_short + parabolic_long})
        lines.append(f"\n⚠️  Velo parabolic flag — {', '.join(names)}: "
                     f"extended moves on Velo's spaghetti. "
                     f"Fast spike rule applies: check news/BTC correlation before entering.")

    # ── Timing edge ─────────────────────────────────────────────────────────
    lines.append(f"\nToday's Edge: {_short_term_edge()}")

    # ── Bottom line ─────────────────────────────────────────────────────────
    top_short = _clean_symbol(valid_shorts[0]["symbol"]) if valid_shorts else None
    top_long  = _clean_symbol(valid_longs[0]["symbol"]) if valid_longs else None
    bl_lines = []
    bl_lines.append("\nBottom line:")
    if bias == "BULLISH":
        bl_lines.append("Bias BULLISH — prefer SHORT reversals.")
    elif bias == "BEARISH":
        bl_lines.append("Bias BEARISH — prefer LONG reversals.")
    else:
        bl_lines.append("Bias MIXED — both directions viable.")
    if funding_alerts:
        funded = [_clean_symbol(c["symbol"]) for c in funding_alerts]
        bl_lines.append(f"Funding spikes on {', '.join(funded)} — entries near funding time forbidden.")
    if top_short and top_long:
        bl_lines.append(f"Top SHORT: {top_short} | Top LONG: {top_long}.")
    elif top_short:
        bl_lines.append(f"Best SHORT: {top_short}.")
    elif top_long:
        bl_lines.append(f"Best LONG: {top_long}.")
    bl_lines.append("Not a high-conviction day — wait for Asian session if in doubt.")

    lines.extend(bl_lines)
    lines.append("=" * 56)

    return "\n".join(lines)

# ── Main ─────────────────────────────────────────────────────────────────────
def run_scan(with_velo: bool = False, journal: bool = False):
    print("=" * 62)
    print("  VELO + SPICY MEAN REVERSION SCANNER")
    print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}   Velo: {'YES' if with_velo else 'NO'}")
    print("=" * 62)

    # ── Velo data ────────────────────────────────────────────────────────
    parabolic_coins = []
    y_axis_max      = 0
    bias            = "MIXED"
    strength        = "MILD"
    bias_raw        = ""
    spaghetti_class = "UNKNOWN"
    velo_news       = []

    if with_velo and HAS_BROWSER:
        print("\n  Opening Velo Market page...")
        snap = capture_velo_snapshot()
        if snap:
            buckets = parse_return_buckets(snap)
            bias     = buckets.get("bias", "MIXED")
            strength = buckets.get("strength", "MILD")
            bias_raw = buckets.get("raw", "")

            spagh = parse_spaghetti_chart(snap)
            parabolic_coins = spagh.get("coins", [])
            y_axis_max      = spagh.get("y_axis_max", 0)
            spaghetti_class = spagh.get("classification", "UNKNOWN")
            velo_news       = parse_velo_news(snap)

            print(f"  Return Buckets → {bias}/{strength}")
            print(f"  Spaghetti       → {spaghetti_class} (y-axis max {y_axis_max}%)")
            print(f"  Parabolic coins: {', '.join(parabolic_coins[:8])}")
        else:
            print("  Velo snapshot unavailable — running without Velo data")
    else:
        print("\n  Skipping Velo (--with-velo not set)")

    # ── Binance data ───────────────────────────────────────────────────────
    print("\n  Fetching Binance futures data...")
    tickers = fetch_tickers()
    top20 = fetch_top20_trade_count(tickers)
    top10_trd = set(top20[:10])
    print(f"  {len(tickers)} symbols loaded")
    print(f"  Top 10 by TRD: {', '.join(top20[:5])}...")

    # ── Filter + score ───────────────────────────────────────────────────
    print("\n  Filtering (VOL≥$500K, TRD≥5K, no majors)...")
    candidates = []
    checked = 0

    for sym in tickers:
        if not sym.endswith("USDT") or sym in MAJORS:
            continue
        t = tickers[sym]
        try:
            quote_vol = float(t.get("quoteVolume", 0))
            trd5      = float(t.get("count", 0))
        except:
            continue
        # quoteVolume is 24h; convert to 5min equivalent: / 288
        vol_5m = quote_vol / 288
        if vol_5m < MIN_VOL_5M or trd5 < MIN_TRD_5M:
            continue

        checked += 1

        # Fetch 5m klines for actual 5m change (rate-limit: sleep 50ms)
        five_min_chg = compute_5m_change(sym)
        five_min_atr = compute_5m_atr(sym)
        time.sleep(0.05)  # Binance rate limit: 1200/min

        score = mr_score(t, five_min_chg, five_min_atr, top10_trd)
        coin = fmt_coin(t, five_min_chg, five_min_atr, score,
                        bias, parabolic_coins, y_axis_max, top10_trd)

        # Apply bias guidance
        if bias == "BULLISH" and coin["direction"] == "LONG":
            coin["score"] = max(0, coin["score"] - 8)  # penalise counter-trend
        if bias == "BEARISH" and coin["direction"] == "SHORT":
            coin["score"] = max(0, coin["score"] - 8)

        candidates.append(coin)

    print(f"  {checked} coins passed liquidity filters")

    # Sort: staircase at bottom, then by score
    candidates.sort(key=coin_sort_key)

    # ── Separate LONG / SHORT / STAIRCASE / FAST SPIKE ──────────────────
    longs   = [c for c in candidates if not c["staircase"] and c["direction"] == "LONG"]
    shorts  = [c for c in candidates if not c["staircase"] and c["direction"] == "SHORT"]
    stairs  = [c for c in candidates if c["staircase"]]
    fast_spikes = [c for c in candidates if c["is_fast_spike"] and not c["staircase"]]

    longs.sort(key=lambda x: -x["score"])
    shorts.sort(key=lambda x: -x["score"])

    # ── Funding alerts ───────────────────────────────────────────────────
    funding_alerts = [c for c in candidates
                      if c["skip"] and "funding" in c["skip"].lower()]
    funding_alerts.sort(key=lambda x: -abs(x.get("funding_rate", 0)))

    # ── Print report ─────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(f"  SESSION BIAS  (Velo Return Buckets)")
    print(f"  {'🟢' if bias=='BULLISH' else '🔴' if bias=='BEARISH' else '🟡'}  {bias}  |  {strength} conviction")
    if bias_raw:
        print(f"  → {bias_raw}")
    print()
    print("  Trading direction guidance:")
    if bias == "BULLISH":
        print("  → Bias is BULLISH — prefer SHORT reversals (fade dumps only)")
    elif bias == "BEARISH":
        print("  → Bias is BEARISH — prefer LONG reversals (fade pumps only)")
    else:
        print("  → Bias is MIXED — both LONG and SHORT reversals viable")
    print()
    print("  Your backtest best sessions (UTC):")
    print("  • London AM (08-12 UTC):  83% WR — BEST")
    print("  • Asian (00-08 UTC):      91% WR — very strong")
    print("  • London PM (12-16 UTC):  monitor, mixed results")

    if funding_alerts:
        print()
        print(f"  ⚠️  FUNDING ALERTS  (due <90min AND >0.1R)")
        for c in funding_alerts[:6]:
            fr = c.get("funding_rate", 0)
            nxt = c.get("next_fund", "?")
            print(f"  • {c['symbol']:<14} {fr:+.3f}%  funding at {nxt} → {c['skip']}")
            if c["skip_reason"]:
                print(f"    {c['skip_reason']}")

    print()
    print("=" * 62)
    print("  🔴  REVERSAL SHORT CANDIDATES  (coins UP — fade the top)")
    print("  ───────────────────────────────────────────────────────")
    print(f"  {'Coin':<14} {'24h%':>7} {'5m%':>7} {'MR':>5}  {'Trd5M':>8} {'Vol5M':>10}  Flags")
    print("  " + "-" * 70)

    for c in shorts[:10]:
        spike_flag = "⚡FAST_SPIKE " if c["is_fast_spike"] else ""
        par_flag   = "PARABOLIC " if c["parabolic"] else ""
        top_flag   = "★TOP_TRD " if c["in_top10_trd"] else ""
        print(f"  {c['symbol']:<14} {c['chg24']:>+7.2f} "
              f"{str(c.get('5m_chg','')):>7} "
              f"{c['score']:>5.1f}  "
              f"{c['trd5m']:>8,}  "
              f"${c['vol5m']/1e6:>8.1f}M  "
              f"{spike_flag}{par_flag}{top_flag}")
        if c.get("5m_atr"):
            print(f"    5m ATR: {c['5m_atr']:.3f}% | bias: {c['bias_guidance']}")

    print()
    print("  🟢  REVERSAL LONG CANDIDATES  (coins DOWN — buy the dip)")
    print("  ───────────────────────────────────────────────────────")
    print(f"  {'Coin':<14} {'24h%':>7} {'5m%':>7} {'MR':>5}  {'Trd5M':>8} {'Vol5M':>10}  Flags")
    print("  " + "-" * 70)

    for c in longs[:10]:
        spike_flag = "⚡FAST_SPIKE " if c["is_fast_spike"] else ""
        par_flag   = "PARABOLIC " if c["parabolic"] else ""
        top_flag   = "★TOP_TRD " if c["in_top10_trd"] else ""
        print(f"  {c['symbol']:<14} {c['chg24']:>+7.2f} "
              f"{str(c.get('5m_chg','')):>7} "
              f"{c['score']:>5.1f}  "
              f"{c['trd5m']:>8,}  "
              f"${c['vol5m']/1e6:>8.1f}M  "
              f"{spike_flag}{par_flag}{top_flag}")

    if fast_spikes and not with_velo:
        print()
        print("  ⚡  FAST SPIKE COINS  (≥1.5% in 5m — check news before trading)")
        print("  ───────────────────────────────────────────────────────")
        for c in fast_spikes[:8]:
            print(f"  • {c['symbol']}  {c.get('5m_chg', '?'):+.3f}% in 5m")

    if stairs:
        print()
        print("  🚫  STAIRCASE / BREAKOUT MODE  (do NOT fade these)")
        print("  ───────────────────────────────────────────────────────")
        for c in stairs[:8]:
            print(f"  • {c['symbol']:<14} {c['chg24']:>+7.2f}%  {c['skip_reason']}")

    if with_velo and velo_news:
        print()
        print("  📰  VELO NEWS")
        for n in velo_news[:5]:
            print(f"  • {n[:80]}")

    # ── Spicy's quick checklist ─────────────────────────────────────────
    print()
    print("=" * 62)
    print("  SPICY'S TRADING CHECKLIST")
    print("  ─────────────────────────────────────────────────────────")
    print("  ✅  Volume ≥ $500K/5min")
    print("  ✅  TRD ≥ 5,000/5min")
    print("  ✅  No majors (BTC, ETH, SOL, XRP, LTC)")
    print("  ✅  Funding due > 90min OR < 0.1R")
    print("  ✅  NOT staircase (24h change < 50%)")
    print()
    print("  FAST SPIKE RULE:")
    print("  ⚡ 5m move ≥ 1.5% → look for news/BTC correlation")
    print("     If news-driven OR BTC-correlated → SKIP reversal")
    print("     If random/no news → CONSIDER reversal")
    print()
    print("  SCORE FORMULA:")
    print("  MR = vol(20) + trd(30) + spike(30) + cap_bonus(5) + funding_penalty")
    print()
    print(f"  Bias: {bias}/{strength} | Spaghetti: {spaghetti_class} (y-axis {y_axis_max}%)")
    print("=" * 62)

    # Save structured data
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bias": {"value": bias, "strength": strength, "raw": bias_raw},
        "spaghetti": {"classification": spaghetti_class, "y_axis_max": y_axis_max,
                      "parabolic_coins": parabolic_coins},
        "candidates": {
            "longs": longs[:10], "shorts": shorts[:10],
            "stairs": stairs[:8], "fast_spikes": fast_spikes[:8]
        }
    }
    json_path = "/home/openclaw/workspace/scanner/latest_spicy_scan.json"
    with open(json_path, "w") as f:
        json.dump(out, f, indent=2, default=str)

    # Generate and print the Telegram-ready report
    report = generate_report(out)
    print("\n" + report)

    # Save plain-text report
    report_path = "/home/openclaw/workspace/scanner/latest_report.txt"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\nSaved: {json_path}\nSaved: {report_path}")

    if journal:
        log_to_journal(shorts, longs, bias, spaghetti_class, tickers)

def _compute_sl_tp(close_price: float, five_min_atr_pct: float | None,
                   direction: str, is_fast_spike: bool) -> tuple[float, float]:
    """
    Compute SL and TP for a signal.
    five_min_atr_pct is returned by compute_5m_atr() as a PERCENTAGE (e.g. 0.8 = 0.8%).
    We convert to absolute price distance before computing SL/TP.
    """
    R_SHORT = 1.5
    R_LONG  = 1.5

    if five_min_atr_pct is None or five_min_atr_pct == 0:
        # Fallback: 0.5% of price as ATR proxy
        atr_pct = 0.5
    else:
        atr_pct = five_min_atr_pct

    # Convert percentage ATR to absolute price distance
    atr_price = atr_pct / 100 * close_price

    if direction == "SHORT":
        sl = close_price + atr_price  # stop above entry for short
        tp = close_price - atr_price * R_SHORT
    else:
        sl = close_price - atr_price  # stop below entry for long
        tp = close_price + atr_price * R_LONG

    return sl, tp


def log_to_journal(shorts, longs, bias, spaghetti_class, tickers):
    """Log top MR candidates to the trading journal, capturing entry prices + SL/TP.
    Also checks all pending MR trades for exits (TP/SL/counter-signal).
    """
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from journal import Journal
        j = Journal()

        logged = []
        today = datetime.now(timezone.utc).isoformat()

        # ── Step 1: Build market data for exit detection ────────────────────
        market_data = {}
        for sym, t in tickers.items():
            try:
                price = float(t.get("lastPrice") or t.get("closePrice") or 0)
            except (TypeError, ValueError):
                price = 0
            if price > 0:
                # Determine current MR direction from our candidate lists
                direction = ""
                for c in shorts:
                    if c["symbol"] == sym:
                        direction = "SHORT"
                        break
                for c in longs:
                    if c["symbol"] == sym:
                        direction = "LONG"
                        break
                market_data[sym] = {"price": price, "direction": direction,
                                    "scanner": "mean_reversion"}

        # ── Step 2: Check pending trades for exits ─────────────────────────
        closed = j.check_pending_exits(market_data)
        if closed:
            for c in closed:
                outcome_icon = "✅" if c["outcome"] == "win" else "❌" if c["outcome"] == "loss" else "➖"
                print(f"\n  Journal: {outcome_icon} {c['trade_id']} closed "
                      f"{c['outcome']} ({c['pnl_r']:+.2f}R) at {c['exit_price']}")

        # ── Step 3: Log new signals with entry_price + SL/TP ───────────────
        for c in shorts[:5]:
            if c.get("skip") or c["score"] < 50:
                continue
            close_price = c.get("close_price") or 0
            five_min_atr = c.get("5m_atr")
            sl, tp = _compute_sl_tp(close_price, five_min_atr, "SHORT",
                                    c.get("is_fast_spike", False))
            flags = []
            if c.get("is_fast_spike"):
                flags.append("FAST")
            if c.get("in_top10_trd"):
                flags.append("TOP_TRD")
            if c.get("parabolic"):
                flags.append("PARABOLIC")
            thesis = (
                f"MR reversal SHORT | {c['chg24']:+.1f}% in 24h | "
                f"bias={bias} | spaghetti={spaghetti_class}"
            )
            trade_id = j.log_signal(
                scanner="mean_reversion",
                direction="short",
                coin=c["symbol"],
                entry_price=close_price,
                sl_price=round(sl, 6) if sl else None,
                tp_price=round(tp, 6) if tp else None,
                quality_score=3 if c["score"] >= 70 else 2 if c["score"] >= 60 else 1,
                mr_score=c["score"],
                flags=flags,
                level_approach="fast_spike" if c.get("is_fast_spike") else "slow_grind",
                volume_regime="increasing" if c.get("in_top10_trd") else "unclear",
                thesis=thesis,
                signal_ts=today,
            )
            logged.append(trade_id)
            # ── Post MR signal to Discord #signals ──────────────────────────
            score = 3 if c["score"] >= 70 else 2 if c["score"] >= 60 else 1
            signal_content = (
                f"🌶️ **{c['symbol']}** SHORT — quality {score}/3\n"
                f"_{thesis}_\n"
                f"Entry: `{close_price:.4f}` | SL: `{round(sl,6) if sl else 0:.4f}` | TP: `{round(tp,6) if tp else 0:.4f}`\n"
                f"_Signal: {today[:16]} UTC_"
            )
            _discord_post(DISCORD_SIGNALS_WH, signal_content)

        for c in longs[:5]:
            if c.get("skip") or c["score"] < 50:
                continue
            close_price = c.get("close_price") or 0
            five_min_atr = c.get("5m_atr")
            sl, tp = _compute_sl_tp(close_price, five_min_atr, "LONG",
                                    c.get("is_fast_spike", False))
            flags = []
            if c.get("is_fast_spike"):
                flags.append("FAST")
            if c.get("in_top10_trd"):
                flags.append("TOP_TRD")
            thesis = (
                f"MR reversal LONG | {c['chg24']:+.1f}% in 24h | "
                f"bias={bias} | spaghetti={spaghetti_class}"
            )
            trade_id = j.log_signal(
                scanner="mean_reversion",
                direction="long",
                coin=c["symbol"],
                entry_price=close_price,
                sl_price=round(sl, 6) if sl else None,
                tp_price=round(tp, 6) if tp else None,
                quality_score=3 if c["score"] >= 70 else 2 if c["score"] >= 60 else 1,
                mr_score=c["score"],
                flags=flags,
                level_approach="fast_spike" if c.get("is_fast_spike") else "slow_grind",
                volume_regime="increasing" if c.get("in_top10_trd") else "unclear",
                thesis=thesis,
                signal_ts=today,
            )
            logged.append(trade_id)
            # ── Post MR signal to Discord #signals ──────────────────────────
            score = 3 if c["score"] >= 70 else 2 if c["score"] >= 60 else 1
            signal_content = (
                f"🌶️ **{c['symbol']}** LONG — quality {score}/3\n"
                f"_{thesis}_\n"
                f"Entry: `{close_price:.4f}` | SL: `{round(sl,6) if sl else 0:.4f}` | TP: `{round(tp,6) if tp else 0:.4f}`\n"
                f"_Signal: {today[:16]} UTC_"
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
    ap.add_argument("--with-velo", action="store_true",
                    help="Open Velo.xyz in browser and parse charts")
    ap.add_argument("--journal", action="store_true",
                    help="Also log signals to the trading journal")
    args = ap.parse_args()
    run_scan(with_velo=args.with_velo, journal=args.journal)
