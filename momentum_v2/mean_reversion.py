"""
Mean Reversion v2 — @spicyofc "Mean Reversion Trading Strategy Guide"
(part 1 = P1, part 2 / 50 trade examples = P2). Paper only.

Built on the same engine as the momentum bot (core.py): closed 1-minute
candles, limit/market fill simulation, stop/target/evolving-stop/time
management, net-of-fees R. This file adds the three reversal setups and the
reversal regime check.

Regime ("easy mode", P1 p12 / P2 p7 cheat sheet) — score 0-3:
  V1 fast spike into the level (not a slow grind)          P1 p13-19, p64
  V2 volume NOT increasing (flat or decreasing)            P1 p20-23, p66-68
  V3 sideways / ranging price action on the left           P1 p26-36
  3/3 high quality, 2/3 take, 1/3 normally skip — except a long-running
  range (duration of regime ≥ 4h, P2 p13/p12), 0/3 never.
  Veto: a 2h+ staircase AGAINST the trade outweighs everything (P2 p55-70).

Setups (P1 chapter 7):
  A. Failed breakout (p94-110): candle closes outside the range, then a
     candle closes back inside (a one-candle wick = SFP counts, P2 p20).
     MUST be ranging AND volume not increasing. Target = other side of the
     range. Stop = next S/R beyond if it is roughly 1R, else 1R.
     Range ≥ 3% → market, < 3% → limit on the failed level.
  B. Fast spike reversal (p111-129): spike into a key level, close back
     inside, swing forms, candle closes through that swing (break of
     structure). Spike > 1.5% → market, else limit on the broken swing.
     Target = origin of the spike, stop = spike extreme (1R if that gives
     R:R < 1, P2 p37). Skip if BTC spiked at the same time, if the spike did
     not reach a key level, or if structure took > 90 min to break.
  C. Partial fill / top tick (p130-139): a candle hits EXACTLY the price of
     a prior swing high (low) on above-average volume, next candle closes the
     other way → market order. Target = recent swing low (high). Stop = 1R.

Common rules: no move < 1% after fees (P2 p16, p33); limits cancelled at 90%
of the way to target or after 60 min (P2 p22); +0.9R → stop near breakeven
(P2 p34-35); cut at 2 hours (P2 p25, p42); funding > 0.1R due within 90 min
→ skip (P1 p92, live only).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .core import (Bar, LevelContext, SymbolEngine, Trade, efficiency_ratio,
                   is_pivot_high, is_pivot_low, pivots, resample_n, staircase)


@dataclass
class MRConfig(Config):
    # ── management (P2) ──────────────────────────────────────────────────────
    limit_expiry_bars: int = 60          # P2 p22: cancel if not filled within 60 min
    missed_fraction: float = 0.9         # P2 p22: cancel at 90% of the way to target
    min_target_pct: float = 0.01         # P2 p16/p33: moves < 1% not worth it after fees
    evolve_trigger_r: float = 0.9        # P2 p35
    evolve_stop_r: float = 0.05          # P2 p35: "within 0R - 0.1R"
    time_stop_min: int = 120             # P2 p25
    early_cut_enabled: bool = False      # not a rule in the MR guide
    min_quote_vol_per_min: float = 100_000   # P1 p89: $500k / 5 min

    # ── entry order type ─────────────────────────────────────────────────────
    fb_market_range_pct: float = 0.03    # P1 p98: range ≥ 3% → market
    spike_market_pct: float = 0.015      # P1 p118: spike > 1.5% → market

    # ── V1 fast spike (P1 p64: size, slope, volume) ─ ASSUMPTIONS ────────────
    spike_window_bars: int = 10          # P1 p38: approach = the last ~10 minutes
    spike_min_pct: float = 0.01          # spike at least 1% origin→extreme
    spike_range_mult: float = 5.0        # move ≥ 5× the average 1m candle (2h)
    spike_max_bars: int = 5              # steep: done within 5 candles
    spike_vol_mult: float = 3.0          # a spike candle has ≥ 3× median 2h volume

    # ── V2 volume not increasing (P1 p66-68) ─ ASSUMPTIONS ───────────────────
    # "average volume now vs at the start of the regime", recent data weighted
    # more (P2 p63): both checks must pass.
    vol_regime_max_ratio: float = 1.10   # last hour avg vs the hour 3-4h ago
    vol_recent_max_ratio: float = 1.15   # last hour vs the hour before
    vol_unclear_max_ratio: float = 1.30  # "slightly increasing / unclear" (P2 #2, #20) — not clearly rising

    # ── V3 ranging (P1 p36 MA-cross tip, P1 p84) ─ ASSUMPTIONS ───────────────
    range_bars: int = 120
    range_ma: int = 20
    range_min_crosses: int = 6           # price crosses its 20-MA ≥ 6 times in 2h
    range_max_abs_er: float = 0.35       # 5m efficiency ratio over 2h
    dor_exception_hours: int = 4         # P2 p13: 4h+ range can carry a 1/3 trade

    # ── setups ───────────────────────────────────────────────────────────────
    fb_range_bars: int = 120             # range = the 2h before the breakout
    fb_max_outside_bars: int = 15        # breakout must fail within 15 min (ASSUMPTION)
    sl_level_r_band: tuple = (0.7, 1.3)  # "next level is roughly 1R" (P1 p105)
    spike_max_bos_bars: int = 90         # P1 p127: > 90 min = warning → skip
    level_touch_tol: float = 0.001       # spike must reach within 0.1% of a key level
    btc_corr_pct: float = 0.005          # BTC moved ≥ 0.5% the same way during the spike → skip
    tt_lookback_bars: int = 360          # swing highs from the last 6h are "relevant"
    tt_swing_strength: int = 2
    tt_vol_mult: float = 2.0             # top-tick candle volume ≥ 2× 1h average (P1 p138: more = better)

    news_mode: str = "shadow"            # shadow | enforce | off (env NEWS_MODE overrides) — see news.py
    news_lookback_min: int = 120         # news in the 2h before the spike counts
    history_bars: int = 400              # ≥ 6h so duration-of-regime can reach the 4h exception
    max_open_positions: int = 5
    # P1 p108 "the same S/R levels are SUCCESSFULLY holding price": each edge needs
    # ≥ 3 swings within 5% of the range height (on random-walk data this cut noise
    # failed-breakout signals from ~8/day to ~1/day per coin).
    fb_min_edge_tests: int = 3
    fb_edge_band: float = 0.05
    fb_min_range_hours: int = 0          # optional: range must have lasted this long


# ── regime variables ─────────────────────────────────────────────────────────
def find_spike(bars: list[Bar], end: int, d_spike: int, cfg: MRConfig) -> Optional[dict]:
    """Was the move into bars[end] a fast spike? d_spike=+1 for an up-spike.
    P1 p64: judged on SIZE, SLOPE and VOLUME.
      slope/size: the move over the last `spike_max_bars` candles alone must be
                  ≥ spike_min_pct and ≥ spike_range_mult × the average 2h candle
      volume:     a candle in that burst has ≥ spike_vol_mult × median 2h volume
    The ORIGIN (the target, P1 p121) is where the leg started: walk back while
    each earlier candle's low (high) is lower (higher)."""
    if end < cfg.range_bars + cfg.spike_window_bars:
        return None
    ext = bars[end].h if d_spike == 1 else bars[end].l
    burst = bars[end - cfg.spike_max_bars:end + 1]
    b_origin = min(b.l for b in burst) if d_spike == 1 else max(b.h for b in burst)
    size = (ext - b_origin) * d_spike / b_origin
    base = bars[end - cfg.range_bars - cfg.spike_window_bars:end - cfg.spike_window_bars]
    avg_rng = sum(b.h - b.l for b in base) / len(base) or 1e-12
    med_vol = statistics.median(b.v for b in base) or 1e-12
    vol_peak = max(b.v for b in burst) / med_vol
    if not (size >= cfg.spike_min_pct and abs(ext - b_origin) >= cfg.spike_range_mult * avg_rng
            and vol_peak >= cfg.spike_vol_mult):
        return None
    k = end
    lim = end - cfg.spike_window_bars
    while k - 1 >= lim and ((bars[k - 1].l < bars[k].l) if d_spike == 1 else (bars[k - 1].h > bars[k].h)):
        k -= 1
    origin = bars[k].l if d_spike == 1 else bars[k].h
    return {"origin": origin, "extreme": ext, "origin_i": k, "size_pct": round(size * 100, 2),
            "bars": end - k, "vol_x": round(vol_peak, 1)}


def volume_not_increasing(bars: list[Bar], cfg: MRConfig) -> tuple[bool, dict]:
    if len(bars) < 250:
        return False, {"reason": "not enough history"}
    v = [b.v for b in bars]
    recent = sum(v[-70:-10]) / 60          # last hour, excluding the approach itself
    prior = sum(v[-130:-70]) / 60
    start = sum(v[-250:-190]) / 60         # ~3-4h ago ≈ start of the regime
    r_regime = recent / start if start else 9.9
    r_recent = recent / prior if prior else 9.9
    ok = r_regime <= cfg.vol_regime_max_ratio and r_recent <= cfg.vol_recent_max_ratio
    return ok, {"vol_vs_regime_start": round(r_regime, 2), "vol_vs_prev_hour": round(r_recent, 2)}


def _ma_crosses(window: list[Bar], n: int) -> int:
    closes = [b.c for b in window]
    crosses, prev = 0, None
    for i in range(n, len(closes)):
        ma = sum(closes[i - n:i]) / n
        side = closes[i] > ma
        if prev is not None and side != prev:
            crosses += 1
        prev = side
    return crosses


def is_ranging(window: list[Bar], d_trade: int, cfg: MRConfig) -> tuple[bool, dict]:
    """V3. A sideways range, or a choppy trend in the trade's direction
    (P1 p84: choppy uptrend is fine for reversal longs, choppy downtrend for shorts)."""
    crosses = _ma_crosses(window, cfg.range_ma)
    er = efficiency_ratio([b.c for b in resample_n(window, 5)])
    choppy = crosses >= cfg.range_min_crosses
    ok = choppy and (abs(er) <= cfg.range_max_abs_er or er * d_trade > 0)
    return ok, {"ma_crosses_2h": crosses, "er_2h": round(er, 3)}


def range_duration_hours(bars: list[Bar], cfg: MRConfig) -> int:
    """Duration of regime (P2 p13): how many trailing hours still look like a range."""
    hours = 0
    for h in range(2, len(bars) // 60 + 1):
        w = bars[-60 * h:]
        er = abs(efficiency_ratio([b.c for b in resample_n(w, 5)]))
        if er <= cfg.range_max_abs_er and _ma_crosses(w, cfg.range_ma) >= cfg.range_min_crosses * h / 2:
            hours = h
        else:
            break
    return hours


# ── engine ───────────────────────────────────────────────────────────────────
class MeanReversionEngine(SymbolEngine):
    """Same order/position handling as the momentum engine, different signals."""

    def _check_signal(self, bars: list[Bar], ctx: LevelContext, allowed) -> Optional[Trade]:
        cfg: MRConfig = self.cfg
        if len(bars) < 252:
            return None
        hour = bars[-60:]
        if sum(b.qv for b in hour) / 60 < cfg.min_quote_vol_per_min:
            return None
        for finder in (self._fast_spike, self._partial_fill, self._failed_breakout):
            for side in ("long", "short"):
                if side not in allowed:
                    continue
                t = finder(bars, ctx, side)
                if t:
                    return t
        return None

    # -- shared scoring ---------------------------------------------------------
    def _score(self, bars, d, spike_found: bool, approach_end: int) -> tuple[int, dict, dict, bool]:
        cfg: MRConfig = self.cfg
        window = bars[approach_end - cfg.range_bars - cfg.spike_window_bars:approach_end - cfg.spike_window_bars]
        # volume up to the approach: the spike's own candles are excluded (they are the
        # last 10 of this slice), so a later break of structure can't count them
        v2, d2 = volume_not_increasing(bars[:approach_end + 1], cfg)
        v3, d3 = is_ranging(window, d, cfg)
        crit = {"fast_spike": spike_found, "volume_not_increasing": v2, "ranging": v3}
        score = sum(crit.values())
        dor = range_duration_hours(bars[:approach_end - cfg.spike_window_bars], cfg)
        # Veto: 2h+ staircase against the reversal (P2 p55-70). Uses the
        # momentum guide's staircase test, in the breakout's direction.
        against, ds = staircase(bars[:approach_end - cfg.spike_window_bars + 1], -d, cfg)
        ok = not against and (score >= 2 or (score == 1 and v3 and dor >= cfg.dor_exception_hours))
        return score, crit, {**d2, **d3, "range_hours": dor, "staircase_against": against,
                             "staircase_detail": ds}, ok

    def _stop_from_level_or_1r(self, ctx, d, entry, target, beyond, now) -> tuple[float, str]:
        """P1 p105-107: next S/R beyond the failed level if it is roughly 1R, else 1R."""
        cfg: MRConfig = self.cfg
        dist_t = abs(target - entry)
        lvl = ctx.next_resistance(beyond, now) if d == -1 else ctx.next_support(beyond, now)
        lo, hi = cfg.sl_level_r_band
        if lvl is not None and lo * dist_t <= abs(lvl - entry) <= hi * dist_t:
            return lvl, "level"
        return entry - d * dist_t, "1R"

    def _new_trade(self, side, setup, level, ref_entry, entry_type, stop, stop_kind, target,
                   target_kind, score, crit, details, now) -> Optional[Trade]:
        cfg: MRConfig = self.cfg
        d = 1 if side == "long" else -1
        if (target - ref_entry) * d / ref_entry < cfg.min_target_pct:
            return None
        if (ref_entry - stop) * d <= 0:
            return None
        details = {**details, "ref_entry": ref_entry}
        return Trade(symbol=self.symbol, side=side, level_tf=setup, level=level, signal_t=now,
                     entry_type=entry_type, stop=stop, target=target, target_kind=target_kind,
                     score=score, criteria=crit, details=details, setup=setup, stop_kind=stop_kind)

    # -- A. failed breakout -------------------------------------------------------
    def _excursion(self, bars, d):
        """Find a failed break of the range edge that ends on this candle.
        Returns (edge, r_hi, r_lo, extreme_index) or None. d=+1: failed breakdown
        of the range low (→ long); d=-1: failed breakout of the range high (→ short)."""
        cfg: MRConfig = self.cfg
        i = len(bars) - 1
        cur = bars[i]

        def edges(s):
            w = bars[s - cfg.fb_range_bars:s]
            return max(b.h for b in w), min(b.l for b in w)

        def held(s, r_hi, r_lo):
            """P1 p108: 'the same S/R levels are successfully holding price' —
            both edges must have been tested (swing within 15% of the range
            height) at least fb_min_edge_tests times inside the window."""
            w = bars[s - cfg.fb_range_bars:s]
            band = cfg.fb_edge_band * (r_hi - r_lo)
            hs, ls = pivots(w, 2)
            top = sum(1 for _, p in hs if p >= r_hi - band)
            bot = sum(1 for _, p in ls if p <= r_lo + band)
            return top >= cfg.fb_min_edge_tests and bot >= cfg.fb_min_edge_tests

        def outside(b, edge):
            return b.c < edge if d == 1 else b.c > edge

        # (1) SFP: this one candle wicked through the edge and closed back inside (P2 p20)
        r_hi, r_lo = edges(i)
        edge = r_lo if d == 1 else r_hi
        wick = cur.l < edge if d == 1 else cur.h > edge
        if wick and not outside(cur, edge) and not outside(bars[i - 1], edge):
            return (edge, r_hi, r_lo, i) if held(i, r_hi, r_lo) else None
        # (2) one or more candles CLOSED outside, and this candle closed back inside (P1 p96-97)
        for m in range(1, cfg.fb_max_outside_bars + 1):
            s = i - m
            if s - cfg.fb_range_bars < 0:
                return None
            r_hi, r_lo = edges(s)
            edge = r_lo if d == 1 else r_hi
            if outside(cur, edge) or outside(bars[s - 1], edge):
                continue
            if all(outside(b, edge) for b in bars[s:i]):
                if not held(s, r_hi, r_lo):
                    return None
                rng = range(s, i + 1)
                ext_i = min(rng, key=lambda k: bars[k].l) if d == 1 else max(rng, key=lambda k: bars[k].h)
                return edge, r_hi, r_lo, ext_i
        return None

    def _failed_breakout(self, bars, ctx, side) -> Optional[Trade]:
        cfg: MRConfig = self.cfg
        d = 1 if side == "long" else -1
        if len(bars) - 1 - cfg.fb_range_bars - cfg.fb_max_outside_bars < 0:
            return None
        x = self._excursion(bars, d)
        if not x:
            return None
        edge, r_hi, r_lo, ext_i = x
        cur = bars[-1]
        now = cur.t + cfg.bar_ms
        key = f"fb:{side}:{edge}"
        if key in self.attempted:
            return None
        self.attempted[key] = cur.t
        spike = find_spike(bars, ext_i, -d, cfg)
        score, crit, det, ok = self._score(bars, d, spike is not None, ext_i)
        # P1 p108: failed breakouts REQUIRE a range AND volume that is not increasing.
        # P2 trades #2, #15, #20 relax the volume condition when the range has
        # lasted 4h+ ("high DOR can compensate for unclear volume", P2 p13).
        if det["range_hours"] < cfg.fb_min_range_hours:
            return None
        mild = det.get("vol_vs_regime_start", 9) <= cfg.vol_unclear_max_ratio
        long_range = det["range_hours"] >= cfg.dor_exception_hours and mild
        if not ok or not crit["ranging"] or not (crit["volume_not_increasing"] or long_range):
            return None
        target = r_hi if d == 1 else r_lo
        rng = (r_hi - r_lo) / r_lo
        entry_type = "market" if rng >= cfg.fb_market_range_pct else "limit"
        ref = cur.c if entry_type == "market" else edge
        extreme = bars[ext_i].l if d == 1 else bars[ext_i].h
        stop, sk = self._stop_from_level_or_1r(ctx, d, ref, target, extreme, now)
        if (extreme - stop) * d < 0:        # never inside the failed wick
            stop, sk = ref - d * abs(target - ref), "1R"
            if (extreme - stop) * d < 0:
                stop, sk = extreme * (1 - d * 0.0005), "level"
        if sk == "1R":
            sk = "1R_fixed"                 # fixed at signal so the wick guard still holds after a fill
        return self._new_trade(side, "failed_breakout", edge, ref, entry_type, stop, sk, target,
                               "range_opposite", score, crit,
                               {**det, "range_pct": round(rng * 100, 2), "spike": spike}, now)

    # -- B. fast spike reversal --------------------------------------------------------
    def _fast_spike(self, bars, ctx, side) -> Optional[Trade]:
        cfg: MRConfig = self.cfg
        d = 1 if side == "long" else -1          # long = reversal of a DOWN spike
        ds = -d
        i = len(bars) - 1
        cur, prev = bars[i], bars[i - 1]
        now = cur.t + cfg.bar_ms
        lo = max(cfg.range_bars + cfg.spike_window_bars, i - cfg.spike_max_bos_bars)
        # Candidate extremes: candles in the last 90 min that no later candle
        # (including this one) has exceeded — newest first. The spike can stop
        # short of an older high (P1 p111 "fails to break through the level").
        e = sp = L = None
        run_ext = cur.h if ds == 1 else cur.l
        for k in range(i - 1, lo - 1, -1):
            x = bars[k].h if ds == 1 else bars[k].l
            if (x <= run_ext) if ds == 1 else (x >= run_ext):
                continue
            run_ext = x
            prev_x = bars[k - 1].h if ds == 1 else bars[k - 1].l
            if (prev_x >= x) if ds == 1 else (prev_x <= x):
                continue                     # not a peak: the move topped out on an earlier candle
            cand = find_spike(bars, k, ds, cfg)
            if not cand:
                continue
            # P1 p129: the spike must have hit a key level
            lvl = self._key_level_hit(ctx, ds, cand["origin"], x, bars[k].t)
            if lvl is None:
                continue
            e, sp, L = k, cand, lvl
            break
        if e is None:
            return None
        ext = bars[e].h if ds == 1 else bars[e].l
        t_e = bars[e].t
        # a swing formed after the extreme (swing low for an up-spike)
        s = cfg.swing_strength
        piv = None
        for k in range(i - 1 - s, e, -1):
            if (is_pivot_low(bars, k, s) if ds == 1 else is_pivot_high(bars, k, s)):
                piv = bars[k].l if ds == 1 else bars[k].h
                break
        if piv is None:
            return None
        # price must have closed back inside the level, and THIS candle breaks structure
        back_inside = any((b.c < L) if ds == 1 else (b.c > L) for b in bars[e:i + 1])
        bos = (cur.c < piv <= prev.c) if ds == 1 else (cur.c > piv >= prev.c)
        if not (back_inside and bos):
            return None
        key = f"spike:{side}:{t_e}"
        if key in self.attempted:
            return None
        self.attempted[key] = cur.t
        # BTC check (P1 p125): skip if BTC spiked the same way at the same time
        btc = self._btc_move(ctx, bars[sp["origin_i"]].t, t_e + cfg.bar_ms)
        if btc is not None and btc * ds >= cfg.btc_corr_pct:
            return None
        bos_min = i - e
        score, crit, det, ok = self._score(bars, d, True, e)
        if not ok:
            return None
        origin = sp["origin"]
        size = abs(ext - origin) / origin               # P1 p118: origin → extreme
        entry_type = "market" if size > cfg.spike_market_pct else "limit"
        ref = cur.c if entry_type == "market" else piv
        stop, sk = ext * (1 + ds * 0.0005), "level"        # just beyond the spike extreme
        if abs(stop - ref) > abs(origin - ref):             # R:R < 1 → 1R stop (P2 p37)
            stop, sk = ref - d * abs(origin - ref), "1R"
        quality = "good" if bos_min <= 15 else "average" if bos_min <= 30 else "slow"
        return self._new_trade(side, "fast_spike", piv, ref, entry_type, stop, sk, origin,
                               "spike_origin", score, crit,
                               {**det, "spike": sp, "spike_start_t": bars[sp["origin_i"]].t,
                                "key_level": L, "bos_minutes": bos_min,
                                "bos_quality": quality, "btc_move_pct": None if btc is None else round(btc * 100, 2)},
                               now)

    def _key_level_hit(self, ctx, ds, origin, ext, t) -> Optional[float]:
        """A major level (previous 1h/4h/day/week high or low) that the spike ran
        into: in the far half of the spike and reached (within tolerance)."""
        tol = self.cfg.level_touch_tol
        levels = []
        for tf in self.cfg.level_timeframes:
            b = ctx.prev(tf, t)
            if b:
                levels.append(b.h if ds == 1 else b.l)
        mid = origin + 0.5 * (ext - origin)
        hits = [L for L in levels
                if ((mid <= L <= ext * (1 + tol)) if ds == 1 else (ext * (1 - tol) <= L <= mid))]
        if not hits:
            return None
        return max(hits) if ds == 1 else min(hits)

    def _btc_move(self, ctx, t0, t1) -> Optional[float]:
        btc = getattr(ctx, "btc", None)
        if not btc:
            return None
        seg = [b for b in btc.get_range(t0, t1)]
        if not seg:
            return None
        return (seg[-1].c - seg[0].o) / seg[0].o

    # -- C. partial fill / top tick ------------------------------------------------------
    def _partial_fill(self, bars, ctx, side) -> Optional[Trade]:
        cfg: MRConfig = self.cfg
        d = 1 if side == "long" else -1          # long = bottom tick
        i = len(bars) - 1
        tick, cur = bars[i - 1], bars[i]
        now = cur.t + cfg.bar_ms
        P = tick.l if d == 1 else tick.h
        # next candle closes the opposite way and does not take out the tick
        if not ((cur.c > cur.o and cur.l >= P) if d == 1 else (cur.c < cur.o and cur.h <= P)):
            return None
        s = cfg.tt_swing_strength
        lo = max(s, i - 1 - cfg.tt_lookback_bars)
        exact = False
        for k in range(i - 2 - s, lo, -1):
            if d == -1 and is_pivot_high(bars, k, s) and bars[k].h == P:
                exact = True
                break
            if d == 1 and is_pivot_low(bars, k, s) and bars[k].l == P:
                exact = True
                break
        if not exact:
            return None
        # no trade through P between the earlier swing and the tick
        between = bars[k + 1:i - 1]
        if any((b.l < P) if d == 1 else (b.h > P) for b in between):
            return None
        avg_v = sum(b.v for b in bars[-61:-1]) / 60
        if tick.v < cfg.tt_vol_mult * avg_v:
            return None
        key = f"tt:{side}:{P}:{tick.t}"
        if key in self.attempted:
            return None
        self.attempted[key] = cur.t
        spike = find_spike(bars, i - 1, -d, cfg)
        score, crit, det, ok = self._score(bars, d, spike is not None, i - 1)
        if not ok:
            return None
        ref = cur.c
        # target: the most recent swing on the other side, at least 1% away
        target = None
        for k2 in range(i - 1 - cfg.swing_strength, max(0, i - cfg.range_bars), -1):
            if d == -1 and is_pivot_low(bars, k2, cfg.swing_strength):
                p = bars[k2].l
                if (ref - p) / ref >= cfg.min_target_pct:
                    target = p
                    break
            if d == 1 and is_pivot_high(bars, k2, cfg.swing_strength):
                p = bars[k2].h
                if (p - ref) / ref >= cfg.min_target_pct:
                    target = p
                    break
        if target is None:
            return None
        stop, sk = ref - d * abs(target - ref), "1R"       # P1 p136: 1R equidistant
        if (stop - P) * d >= 0:
            return None                                      # a 1R stop would sit inside the tick candle
        return self._new_trade(side, "partial_fill", P, ref, "market", stop, sk, target,
                               "recent_swing", score, crit,
                               {**det, "tick_vol_x": round(tick.v / avg_v, 2), "spike": spike}, now)


class BTCSeries:
    """Time-indexed BTC 1m candles for the correlation check."""

    def __init__(self, bars: list[Bar]):
        self.bars = sorted(bars, key=lambda b: b.t)
        self.ts = [b.t for b in self.bars]

    def get_range(self, t0: int, t1: int) -> list[Bar]:
        import bisect
        a = bisect.bisect_left(self.ts, t0)
        b = bisect.bisect_left(self.ts, t1)
        return self.bars[a:b]


def funding_blocks(rate: float, next_funding_ms: int, now_ms: int, stop_dist_pct: float,
                   side: str | None = None) -> bool:
    """P1 p92: skip if the funding payment is > 0.1R and due within 90 minutes.
    rate and stop_dist_pct are in %. With `side`, only block when this side PAYS
    (positive funding: longs pay shorts; negative: shorts pay longs)."""
    if next_funding_ms is None:
        return False
    if next_funding_ms - now_ms > 90 * 60_000:
        return False
    if side == "long" and rate <= 0 or side == "short" and rate >= 0:
        return False
    return abs(rate) > 0.1 * stop_dist_pct
