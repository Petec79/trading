"""
Momentum v2 — strategy core (pure logic, no network).

The same SymbolEngine is driven by the live paper runner (live.py) and the
backtester (backtest.py), so a backtest result describes exactly what the
live bot will do.

Flow per closed 1-minute candle (see config.py for page references):
  1. Manage any open position (stop / target / evolving stop / trail / time).
  2. Manage any pending limit order (fill on retest / expire / missed).
  3. If flat, look for a candle CLOSING through the previous 1h/4h/1d/1w
     high (long) or low (short), score the 3 criteria, pick stop, entry
     type and target, and place the order.
"""
from __future__ import annotations

import bisect
import statistics
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

from .config import Config, TF_MS, WEEK_OFFSET_MS


# ── Data ─────────────────────────────────────────────────────────────────────
@dataclass
class Bar:
    t: int          # open time, ms UTC
    o: float
    h: float
    l: float
    c: float
    v: float        # base volume
    qv: float = 0.0  # quote (USDT) volume

    @staticmethod
    def from_kline(k: list) -> "Bar":
        return Bar(int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                   float(k[4]), float(k[5]), float(k[7]) if len(k) > 7 else 0.0)


def period_start(t: int, tf: str) -> int:
    if tf == "1M":                                   # calendar month, UTC (Binance-aligned)
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    ms = TF_MS[tf]
    if tf == "1w":
        return ((t - WEEK_OFFSET_MS) // ms) * ms + WEEK_OFFSET_MS
    return (t // ms) * ms


def period_end(t: int, tf: str) -> int:
    """Close time of the candle that opened at (or contains) t."""
    if tf == "1M":
        d = datetime.fromtimestamp(period_start(t, tf) / 1000, tz=timezone.utc)
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return period_start(t, tf) + TF_MS[tf]


def resample(bars: list[Bar], tf: str) -> list[Bar]:
    """Aggregate 1m bars into higher-timeframe candles (Binance-aligned)."""
    out: list[Bar] = []
    for b in bars:
        ps = period_start(b.t, tf)
        if out and out[-1].t == ps:
            x = out[-1]
            x.h = max(x.h, b.h)
            x.l = min(x.l, b.l)
            x.c = b.c
            x.v += b.v
            x.qv += b.qv
        else:
            out.append(Bar(ps, b.o, b.h, b.l, b.c, b.v, b.qv))
    return out


# ── Swing points (p42: 3-candle pivots) ──────────────────────────────────────
def is_pivot_high(bars: list[Bar], k: int, s: int) -> bool:
    if k - s < 0 or k + s >= len(bars):
        return False
    h = bars[k].h
    return all(bars[j].h < h for j in range(k - s, k)) and \
        all(bars[j].h <= h for j in range(k + 1, k + s + 1))


def is_pivot_low(bars: list[Bar], k: int, s: int) -> bool:
    if k - s < 0 or k + s >= len(bars):
        return False
    lo = bars[k].l
    return all(bars[j].l > lo for j in range(k - s, k)) and \
        all(bars[j].l >= lo for j in range(k + 1, k + s + 1))


def pivots(bars: list[Bar], s: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed swing highs and lows, oldest first, as (index, price)."""
    hs, ls = [], []
    for k in range(s, len(bars) - s):
        if is_pivot_high(bars, k, s):
            hs.append((k, bars[k].h))
        if is_pivot_low(bars, k, s):
            ls.append((k, bars[k].l))
    return hs, ls


# ── Levels ───────────────────────────────────────────────────────────────────
class LevelContext:
    """
    Answers "what are the levels at time now_ms?" from higher-timeframe
    candles. Only candles that have fully CLOSED by now_ms are used, so there
    is no look-ahead in backtests.
    """

    def __init__(self, htf: dict[str, list[Bar]], cfg: Config):
        self.cfg = cfg
        self.htf = {tf: sorted(c, key=lambda b: b.t) for tf, c in htf.items()}
        self._closes = {tf: [period_end(b.t, tf) for b in c] for tf, c in self.htf.items()}
        # Swing highs/lows on the target timeframe, with confirmation time.
        tf = cfg.target_swing_tf
        s = cfg.target_swing_strength
        candles = self.htf.get(tf, [])
        hs, ls = pivots(candles, s)
        ms = TF_MS[tf]
        # A pivot at k is known once candle k+s has closed.
        self._swing_hi = [(candles[k].t, candles[k + s].t + ms, p) for k, p in hs]
        self._swing_lo = [(candles[k].t, candles[k + s].t + ms, p) for k, p in ls]

    def prev(self, tf: str, now_ms: int) -> Optional[Bar]:
        """The most recent candle of `tf` that closed at or before now_ms."""
        closes = self._closes.get(tf)
        if not closes:
            return None
        i = bisect.bisect_right(closes, now_ms) - 1
        return self.htf[tf][i] if i >= 0 else None

    def _all_levels(self, now_ms: int, side: str) -> list[float]:
        out = []
        for tf in self.cfg.level_timeframes:
            b = self.prev(tf, now_ms)
            if b:
                out.append(b.h if side == "high" else b.l)
        lo_t = now_ms - self.cfg.target_lookback_days * 86_400_000
        src = self._swing_hi if side == "high" else self._swing_lo
        out += [p for (t, known, p) in src if known <= now_ms and t >= lo_t]
        return out

    def next_resistance(self, price: float, now_ms: int) -> Optional[float]:
        above = [p for p in self._all_levels(now_ms, "high") if p > price * 1.0005]
        return min(above) if above else None

    def next_support(self, price: float, now_ms: int) -> Optional[float]:
        below = [p for p in self._all_levels(now_ms, "low") if p < price * 0.9995]
        return max(below) if below else None


# ── Criteria (p60 cheat sheet) ───────────────────────────────────────────────
def efficiency_ratio(closes: list[float]) -> float:
    """Signed Kaufman efficiency ratio: +1 = straight up, −1 = straight down, ~0 = chop."""
    if len(closes) < 2:
        return 0.0
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path == 0:
        return 0.0
    return (closes[-1] - closes[0]) / path


def staircase(bars: list[Bar], d: int, cfg: Config) -> tuple[bool, dict]:
    """C3: ≥2h of staircase price action in direction d (+1 long, −1 short)."""
    n = cfg.staircase_bars
    if len(bars) < n:
        return False, {"reason": "not enough history"}
    window = bars[-n:]
    buckets = resample_n(window, cfg.staircase_resample)
    closes = [b.c for b in buckets]
    er2 = efficiency_ratio(closes) * d
    er1 = efficiency_ratio(closes[len(closes) // 2:]) * d
    hs, ls = pivots(buckets, 1)
    # Longs: swing lows and highs stepping up. Shorts: stepping down.
    steps = []
    for seq in (ls, hs):
        if len(seq) >= 2:
            ups = sum(1 for i in range(1, len(seq)) if (seq[i][1] - seq[i - 1][1]) * d > 0)
            steps.append(ups / (len(seq) - 1))
    step_ok = all(s >= cfg.staircase_min_step_ratio for s in steps)  # no pullbacks at all = clean trend
    ok = er2 >= cfg.staircase_min_er_2h and er1 >= cfg.staircase_min_er_1h and step_ok
    return ok, {"er_2h": round(er2, 3), "er_1h": round(er1, 3),
                "step_ratios": [round(s, 2) for s in steps]}


def resample_n(bars: list[Bar], n: int) -> list[Bar]:
    out = []
    for i in range(0, len(bars), n):
        chunk = bars[i:i + n]
        out.append(Bar(chunk[0].t, chunk[0].o, max(b.h for b in chunk),
                       min(b.l for b in chunk), chunk[-1].c,
                       sum(b.v for b in chunk), sum(b.qv for b in chunk)))
    return out


def volume_increasing(bars: list[Bar], cfg: Config) -> tuple[bool, dict]:
    """C2: volume in the last hour vs the hour before."""
    n = cfg.staircase_bars
    if len(bars) < n:
        return False, {"reason": "not enough history"}
    half = n // 2
    recent = sum(b.v for b in bars[-half:])
    prior = sum(b.v for b in bars[-n:-half])
    ratio = recent / prior if prior > 0 else 0.0
    return ratio >= cfg.volume_increase_ratio, {"vol_ratio": round(ratio, 2)}


def slow_grind(bars: list[Bar], d: int, cfg: Config) -> tuple[bool, dict]:
    """C1: price ground into the level rather than spiking into it (p36)."""
    n = cfg.staircase_bars
    if len(bars) < n:
        return False, {"reason": "not enough history"}
    ranges = [b.h - b.l for b in bars[-n:]]
    med = statistics.median(ranges) or 1e-12
    avg = sum(ranges) / len(ranges) or 1e-12
    big_bar = max(b.h - b.l for b in bars[-cfg.spike_bars:]) / med
    m = cfg.spike_move_bars
    move = (bars[-1].c - bars[-m - 1].c) * d / avg
    spike = big_bar > cfg.spike_bar_mult or move > cfg.spike_move_atr
    return not spike, {"max_bar_vs_median": round(big_bar, 2), "move_15m_vs_avg": round(move, 2)}


# ── Orders and positions ─────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol: str
    side: str                 # "long" | "short"
    level_tf: str
    level: float
    signal_t: int             # close time of trigger candle (ms)
    entry_type: str           # "limit" | "market"
    stop: float               # initial stop (defines 1R)
    target: Optional[float]   # None → trail mode
    target_kind: str          # "next_level" | "1R" | "trail"
    score: int
    criteria: dict
    details: dict
    status: str = "pending"   # pending | open | closed | cancelled
    entry: Optional[float] = None
    entry_t: Optional[int] = None
    risk: Optional[float] = None
    cur_stop: Optional[float] = None
    stop_state: str = "initial"   # initial | evolved | trailing
    mfe_r: float = 0.0
    early_checked: bool = False
    exit: Optional[float] = None
    exit_t: Optional[int] = None
    exit_reason: Optional[str] = None
    gross_r: Optional[float] = None
    net_r: Optional[float] = None
    journal_id: Optional[str] = None
    bars_waited: int = 0
    setup: str = "momentum_breakout"
    stop_kind: str = "swing"      # "swing" | "level" | "1R" (1R = equidistant from target, re-based on fill)

    @property
    def d(self) -> int:
        return 1 if self.side == "long" else -1

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(x: dict) -> "Trade":
        return Trade(**x)


class SymbolEngine:
    """One symbol, one trade at a time."""

    def __init__(self, symbol: str, cfg: Config):
        self.symbol = symbol
        self.cfg = cfg
        self.trade: Optional[Trade] = None
        self.attempted: dict[str, int] = {}   # level key → signal time, one attempt per level
        self.watched: dict[str, int] = {}     # get-ready alerts already sent (in memory only)
        self.closed: list[Trade] = []

    # -- persistence ----------------------------------------------------------
    def state(self) -> dict:
        return {"trade": self.trade.to_dict() if self.trade else None,
                "attempted": self.attempted}

    def load(self, st: dict):
        self.trade = Trade.from_dict(st["trade"]) if st.get("trade") else None
        self.attempted = st.get("attempted", {})

    # -- main entry point -----------------------------------------------------
    def on_bar(self, bars: list[Bar], ctx: LevelContext, allowed: set = frozenset({"long", "short"}),
               can_open: bool = True) -> list[dict]:
        """Process the newest CLOSED bar (bars[-1]). Returns events."""
        ev: list[dict] = []
        bar = bars[-1]
        t = self.trade
        if t and t.status == "open":
            self._manage_open(t, bars, ev)
        elif t and t.status == "pending":
            self._manage_pending(t, bars, ev)
        if self.trade and self.trade.status in ("closed", "cancelled"):
            if self.trade.status == "closed":
                self.closed.append(self.trade)
            self.trade = None
        if self.trade is None and can_open:
            sig = self._check_signal(bars, ctx, allowed)
            if sig:
                self.trade = sig
                ev.append({"type": "signal", "trade": sig})
        # forget old level attempts (> 8 days)
        cutoff = bar.t - 8 * 86_400_000
        self.attempted = {k: v for k, v in self.attempted.items() if v >= cutoff}
        return ev

    # -- get-ready alerts (manual trading) ------------------------------------------
    def _mark_watch(self, key: str, t: int) -> bool:
        cutoff = t - 8 * 3_600_000
        self.watched = {k: v for k, v in self.watched.items() if v >= cutoff}
        if key in self.watched:
            return False
        self.watched[key] = t
        return True

    def watch(self, bars: list[Bar], ctx: LevelContext, allowed=frozenset({"long", "short"})) -> list[dict]:
        """Momentum: price is close to a level and the regime already qualifies —
        the trigger is one candle close away. Uses the same criteria as the trade."""
        cfg = self.cfg
        if self.trade is not None or len(bars) < cfg.staircase_bars + 2:
            return []
        cur = bars[-1]
        now = cur.t + cfg.bar_ms
        out = []
        for tf in cfg.level_timeframes:
            lvl = ctx.prev(tf, now)              # the level the NEXT candle will be judged against
            if not lvl:
                continue
            for side in ("long", "short"):
                if side not in allowed:
                    continue
                d = 1 if side == "long" else -1
                L = lvl.h if d == 1 else lvl.l
                dist = (L - cur.c) * d / cur.c
                if not (0 < dist <= cfg.watch_dist_pct):
                    continue
                if f"{side}:{tf}:{L}" in self.attempted:
                    continue
                c3, _ = staircase(bars, d, cfg)
                if cfg.require_staircase and not c3:
                    continue
                c1, _ = slow_grind(bars, d, cfg)
                c2, _ = volume_increasing(bars, cfg)
                score = int(c1) + int(c2) + int(c3)
                if score < cfg.min_score:
                    continue
                stop = self._find_stop(bars, d, L)
                if stop is None or not self._mark_watch(f"{side}:{tf}:{L}", now):
                    continue
                sd = abs(L - stop) / L
                how = f"LIMIT at {L:g} after the close" if sd < cfg.limit_vs_market_pct else "MARKET right after the close"
                word = "above" if d == 1 else "below"
                out.append({"symbol": self.symbol, "side": side, "setup": "momentum breakout",
                            "bar_close": now,
                            "text": (f"{dist * 100:.2f}% from the previous {tf} {'high' if d == 1 else 'low'} {L:g}. "
                                     f"{side.upper()} on a 1m CLOSE {word} {L:g} → {how}.\n"
                                     f"Stop ~{stop:g} ({sd * 100:.2f}%) | score {score}/3 so far")})
        return out

    # -- signal -----------------------------------------------------------------
    def _check_signal(self, bars: list[Bar], ctx: LevelContext, allowed) -> Optional[Trade]:
        cfg = self.cfg
        if len(bars) < max(cfg.staircase_bars, cfg.spike_move_bars) + 2:
            return None
        cur, prev = bars[-1], bars[-2]
        now = cur.t + cfg.bar_ms          # trigger candle has just closed
        for tf in cfg.level_timeframes:
            for side in ("long", "short"):
                if side not in allowed:
                    continue
                # The level is the period that had closed BEFORE the trigger
                # candle opened (so a break in the last minute of an hour counts).
                c_lvl = ctx.prev(tf, cur.t)
                if not c_lvl:
                    continue
                L = c_lvl.h if side == "long" else c_lvl.l
                d = 1 if side == "long" else -1
                # p49: "after 1 candle CLOSE through the level" — this candle
                # closed through it, the previous one had not.
                if not (cur.c * d > L * d and prev.c * d <= L * d):
                    continue
                key = f"{side}:{tf}:{L}"
                if key in self.attempted:
                    continue
                self.attempted[key] = cur.t
                tr = self._build_trade(bars, ctx, side, tf, L, now)
                if tr:
                    return tr
        return None

    def _find_stop(self, bars: list[Bar], d: int, L: float) -> Optional[float]:
        """p54: stop at the relevant swing low (long) / swing high (short)."""
        cfg = self.cfg
        s = cfg.swing_strength
        lo = max(s, len(bars) - cfg.stop_search_bars)
        for k in range(len(bars) - 1 - s, lo - 1, -1):
            if d == 1 and is_pivot_low(bars, k, s):
                p = bars[k].l
                dist = (L - p) / L
            elif d == -1 and is_pivot_high(bars, k, s):
                p = bars[k].h
                dist = (p - L) / L
            else:
                continue
            if dist < cfg.min_stop_pct:
                continue
            if dist > cfg.max_stop_pct:
                return None
            return p
        return None

    def _build_trade(self, bars, ctx, side, tf, L, now) -> Optional[Trade]:
        cfg = self.cfg
        d = 1 if side == "long" else -1
        # Liquidity (p58): average $ volume per minute over the last hour.
        hour = bars[-60:]
        qv = sum(b.qv for b in hour) / len(hour)
        if cfg.min_quote_vol_per_min and qv < cfg.min_quote_vol_per_min:
            return None
        c1, d1 = slow_grind(bars, d, cfg)
        c2, d2 = volume_increasing(bars, cfg)
        c3, d3 = staircase(bars, d, cfg)
        score = int(c1) + int(c2) + int(c3)
        if score < cfg.min_score or (cfg.require_staircase and not c3):
            return None
        stop = self._find_stop(bars, d, L)
        if stop is None:
            return None
        dist = abs(L - stop) / L
        entry_type = "limit" if dist < cfg.limit_vs_market_pct else "market"
        # Limit fills at the level; a market order fills around this close.
        # Next level must lie beyond both (a candle can break several levels).
        ref = L if entry_type == "limit" else bars[-1].c
        beyond = max(L, bars[-1].c) if d == 1 else min(L, bars[-1].c)
        target, kind = self._target(ctx, side, ref, stop, score, now, beyond)
        return Trade(
            symbol=self.symbol, side=side, level_tf=tf, level=L, signal_t=now,
            entry_type=entry_type, stop=stop, target=target, target_kind=kind,
            score=score,
            criteria={"slow_grind": c1, "volume_increasing": c2, "staircase_2h": c3},
            details={**d1, **d2, **d3, "stop_dist_pct": round(dist * 100, 2),
                     "quote_vol_per_min": round(qv),
                     # where a manual trader would get in: the level (limit) or this close (market)
                     "ref_entry": L if entry_type == "limit" else bars[-1].c},
        )

    def _target(self, ctx, side, entry, stop, score, now, beyond=None) -> tuple[Optional[float], str]:
        cfg = self.cfg
        d = 1 if side == "long" else -1
        risk = abs(entry - stop)
        one_r = entry + d * cfg.safe_target_r * risk
        ref = entry if beyond is None else beyond
        nxt = ctx.next_resistance(ref, now) if d == 1 else ctx.next_support(ref, now)
        if score >= 3:
            if nxt is None:
                return None, "trail"          # nothing left above/below → trail swings
            if abs(nxt - entry) >= cfg.min_level_target_r * risk:
                return nxt, "next_level"
        return one_r, "1R"

    # -- pending limit ------------------------------------------------------------
    def _manage_pending(self, t: Trade, bars: list[Bar], ev: list):
        cfg = self.cfg
        bar = bars[-1]
        d = t.d
        if t.entry_type == "market":
            # p53: market order immediately after the trigger close → next open.
            fill = bar.o * (1 + d * cfg.slippage)
            if (fill - t.stop) * d <= 0:
                return self._cancel(t, bar, "gapped_through_stop", ev)
            if not self._target_ok(t, fill):
                return self._cancel(t, bar, "target_too_close_after_fill", ev)
            self._fill(t, fill, bar.t, ev)
            self._manage_open(t, bars, ev, fill_bar=True)
            return
        t.bars_waited += 1
        touched = bar.l <= t.level if d == 1 else bar.h >= t.level
        if touched:
            fill = min(bar.o, t.level) if d == 1 else max(bar.o, t.level)
            if (fill - t.stop) * d <= 0:
                return self._cancel(t, bar, "gapped_through_stop", ev)
            self._fill(t, fill, bar.t, ev)
            self._manage_open(t, bars, ev, fill_bar=True)
            return
        if t.target is not None:
            trip = t.level + cfg.missed_fraction * (t.target - t.level)
            if bar.h >= trip if d == 1 else bar.l <= trip:
                return self._cancel(t, bar, "missed_ran_to_target", ev)
        if t.bars_waited >= cfg.limit_expiry_bars:
            return self._cancel(t, bar, "limit_expired", ev)

    def _target_ok(self, t: Trade, price: float) -> bool:
        if t.target is None or not self.cfg.min_target_pct:
            return True
        return (t.target - price) * t.d / price >= self.cfg.min_target_pct

    def _fill(self, t: Trade, price: float, ts: int, ev: list):
        t.status = "open"
        t.entry = price
        t.entry_t = ts
        if t.stop_kind == "1R" and t.target is not None:
            # stop equidistant from the target, measured from the real fill
            t.stop = price - t.d * abs(t.target - price)
        t.risk = abs(price - t.stop)
        t.cur_stop = t.stop
        # Re-base on the actual fill: 1R targets move with the fill, and a
        # level target that is now too close (or behind us) falls back to 1R.
        if t.target_kind == "next_level" and \
                (t.target - price) * t.d < self.cfg.min_level_target_r * t.risk:
            t.target_kind = "1R"
        if t.target_kind == "1R":
            t.target = price + t.d * self.cfg.safe_target_r * t.risk
        ev.append({"type": "fill", "trade": t})

    def _cancel(self, t: Trade, bar: Bar, reason: str, ev: list):
        t.status = "cancelled"
        t.exit_reason = reason
        t.exit_t = bar.t + self.cfg.bar_ms
        ev.append({"type": "cancel", "trade": t})

    # -- open position ------------------------------------------------------------
    def _manage_open(self, t: Trade, bars: list[Bar], ev: list, fill_bar: bool = False):
        cfg = self.cfg
        bar = bars[-1]
        d = t.d
        close_t = bar.t + cfg.bar_ms
        # 1. Stop first (conservative when stop and target share a candle).
        hit_stop = bar.l <= t.cur_stop if d == 1 else bar.h >= t.cur_stop
        if hit_stop:
            px = min(bar.o, t.cur_stop) if d == 1 else max(bar.o, t.cur_stop)
            px *= (1 - d * cfg.slippage)
            reason = {"initial": "SL", "evolved": "SL_after_+0.9R", "trailing": "TRAIL_STOP"}[t.stop_state]
            return self._close(t, px, close_t, reason, taker=True, ev=ev)
        # 2. Target (not on the limit-fill candle: we can't know the order inside it).
        if t.target is not None and not (fill_bar and t.entry_type == "limit"):
            if (bar.h >= t.target) if d == 1 else (bar.l <= t.target):
                px = max(bar.o, t.target) if d == 1 else min(bar.o, t.target)
                return self._close(t, px, close_t, "TP", taker=False, ev=ev)
        # 3. Track best excursion and apply the evolving-R rule (p79).
        #    On a limit-fill candle the extreme may have printed before the fill,
        #    so only the close counts there.
        best = bar.c if (fill_bar and t.entry_type == "limit") else (bar.h if d == 1 else bar.l)
        t.mfe_r = max(t.mfe_r, (best - t.entry) * d / t.risk)
        if t.stop_state == "initial" and t.mfe_r >= cfg.evolve_trigger_r:
            new = t.entry + d * cfg.evolve_stop_r * t.risk
            if (new - t.cur_stop) * d > 0:
                t.cur_stop = new
                t.stop_state = "evolved"
                ev.append({"type": "stop_move", "trade": t, "why": "+0.9R → SL to +0.1R"})
        # 4. Trail mode (p57 #3): stop follows each new confirmed swing.
        if t.target_kind == "trail":
            s = cfg.swing_strength
            k = len(bars) - 1 - s
            if k > 0 and bars[k].t >= t.entry_t:
                if d == 1 and is_pivot_low(bars, k, s) and bars[k].l > t.cur_stop and bars[k].l < bar.c:
                    t.cur_stop, t.stop_state = bars[k].l, "trailing"
                    ev.append({"type": "stop_move", "trade": t, "why": "trail to swing low"})
                elif d == -1 and is_pivot_high(bars, k, s) and bars[k].h < t.cur_stop and bars[k].h > bar.c:
                    t.cur_stop, t.stop_state = bars[k].h, "trailing"
                    ev.append({"type": "stop_move", "trade": t, "why": "trail to swing high"})
        # 5. Time rules.
        mins = (close_t - t.entry_t) / 60_000
        if cfg.early_cut_enabled and not t.early_checked and mins >= cfg.early_cut_min:
            t.early_checked = True
            if t.mfe_r < cfg.early_cut_mfe_r:
                px = bar.c * (1 - d * cfg.slippage)
                return self._close(t, px, close_t, "EARLY_CUT_15m", taker=True, ev=ev)
        if mins >= cfg.time_stop_min:
            px = bar.c * (1 - d * cfg.slippage)
            return self._close(t, px, close_t, "TIME_2h", taker=True, ev=ev)

    def _close(self, t: Trade, px: float, ts: int, reason: str, taker: bool, ev: list):
        cfg = self.cfg
        d = t.d
        fee_in = cfg.maker_fee if t.entry_type == "limit" else cfg.taker_fee
        fee_out = cfg.taker_fee if taker else cfg.maker_fee
        gross = (px - t.entry) * d
        fees = t.entry * fee_in + px * fee_out
        t.exit, t.exit_t, t.exit_reason = px, ts, reason
        t.gross_r = round(gross / t.risk, 4)
        t.net_r = round((gross - fees) / t.risk, 4)
        t.status = "closed"
        ev.append({"type": "exit", "trade": t})
