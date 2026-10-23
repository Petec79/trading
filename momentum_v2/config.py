"""
Momentum v2 — configuration.

Every rule here is taken from @spicyofc's "Momentum Strategy Guide" (updated
11 April 2026). Page numbers refer to that PDF. Where the guide is
discretionary ("does this look like a staircase?") the number below is OUR
quantification of it — marked ASSUMPTION — and is the first thing to tune
with the backtest.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    # ── Timeframe ────────────────────────────────────────────────────────────
    # Guide is traded on the 1-minute chart (p46, p62+ case studies).
    bar_ms: int = 60_000

    # ── Levels (WHERE to enter) — p45, p48 ───────────────────────────────────
    # High/low of the previous 1h, 4h, day and week. 1h is the smallest level
    # the author will trade. Checked biggest-first: if one candle breaks
    # several levels at once we label it with the biggest.
    # Momentum p48 "daily levels, weekly levels or even monthly levels"; MR part 2
    # examples trade monthly highs (#21 COMP, bad #11 AGLD).
    level_timeframes: tuple = ("1M", "1w", "1d", "4h", "1h")

    # Resistance/support used for TARGETS ("the next level", p56): the other
    # HTF levels plus 1h swing highs/lows from the last N days.
    target_swing_tf: str = "1h"
    target_swing_strength: int = 2          # ASSUMPTION: 2 candles each side on 1h
    target_lookback_days: int = 7

    # ── Swing points (stops) — p42 ───────────────────────────────────────────
    # "A swing point requires at least 3 candles. The 2nd candle has the
    # highest/lowest extreme" → strength 1 (one candle each side).
    swing_strength: int = 1
    # p77: "the swing point chosen had to be the 2nd swing high, not the 1st,
    # because the distance to the 1st was too small (less than 1%)".
    min_stop_pct: float = 0.01
    max_stop_pct: float = 0.15              # ASSUMPTION: skip absurdly wide stops
    stop_search_bars: int = 240             # look back up to 4h for the swing

    # ── HOW to enter — p52-53 ────────────────────────────────────────────────
    # Distance level→stop < 3% → limit order ON the broken level.
    # Distance ≥ 3%            → market order right after the candle close.
    limit_vs_market_pct: float = 0.03
    # Not stated in the momentum guide; the author's general rule (MR part 2 p22) is to
    # cancel after 60 min, his average trade duration.
    limit_expiry_bars: int = 60
    # Cancel an unfilled limit once price has covered this share of the way
    # from the level to the target (momentum: the whole way).
    missed_fraction: float = 1.0
    # Skip trades whose entry→target move is smaller than this (0 = no check).
    min_target_pct: float = 0.0

    # ── Quality criteria — p60 "Trade Quality Cheat Sheet" ───────────────────
    # C1 slow grind into the level (NOT a fast spike, p36)
    # C2 volume increasing over time (p25, p38)
    # C3 at least 2 hours of staircase price action on the left (p60, p99)
    staircase_bars: int = 120               # 2 hours of 1m bars
    staircase_resample: int = 5             # judge structure on 5m buckets (24 of them)
    # ASSUMPTION: Kaufman efficiency ratio (net move / total path) — a
    # one-directional staircase scores high, a chop scores near 0.
    staircase_min_er_2h: float = 0.30
    # p97-99: "give weight to the RECENT price action" — last hour must also trend.
    staircase_min_er_1h: float = 0.25
    # ASSUMPTION: share of 5m swing lows (longs) / swing highs (shorts) that
    # step in the trade direction.
    staircase_min_step_ratio: float = 0.6

    # Volume: last hour vs the hour before. ASSUMPTION: +15% = "increasing".
    volume_increase_ratio: float = 1.15

    # Fast spike: ASSUMPTION — any of the last `spike_bars` candles has a range
    # > spike_bar_mult × median 2h range, or the last `spike_move_bars`
    # moved > spike_move_atr × the 2h average range.
    spike_bars: int = 5
    spike_bar_mult: float = 4.0
    spike_move_bars: int = 15
    spike_move_atr: float = 8.0

    # p95: "If 2/3 criteria are met but there is no staircase, I do NOT take the trade."
    min_score: int = 2
    require_staircase: bool = True

    # ── Coins — p58 ──────────────────────────────────────────────────────────
    # "Altcoin perpetual futures, ideally doing at least $100k/1min of volume."
    min_quote_vol_per_min: float = 100_000
    majors: tuple = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                     "DOGEUSDT", "ADAUSDT", "LTCUSDT", "AVAXUSDT", "DOTUSDT")
    # p31-33: top gainers → longs, worst losers → shorts, plus "hot" coins by trade count.
    universe_gainers: int = 15
    universe_losers: int = 15
    universe_trade_count: int = 10
    # "Hot" = top by trades in the last 5 minutes (Orion's TRD 5M column, MR guide
    # p88). Only the busiest N coins by 24h count are polled, to bound requests.
    trade_count_minutes: int = 5
    trade_count_candidates: int = 80
    universe_refresh_min: int = 15

    # ── Targets — p56-58 ─────────────────────────────────────────────────────
    # 3/3 → next level. 2/3 → +1R ("play it safe", repeated through ch.7).
    # No level left (all-time-high style breakout): 3/3 → no TP, trail SL to
    # each new swing; 2/3 → +1R. (The "sell wall" option needs the order
    # book and is not automated.)
    safe_target_r: float = 1.0
    # ASSUMPTION: if the next level is closer than this, use the 1R target instead.
    min_level_target_r: float = 1.0

    # ── Trade management — case studies ch.7 ─────────────────────────────────
    # p79 "evolving R rule": at +0.9R move SL to +0.1R.
    evolve_trigger_r: float = 0.9
    evolve_stop_r: float = 0.1
    # p66, p68, p82: cut at the 2-hour mark (2× the ~60 min average winner).
    time_stop_min: int = 120
    # p75 TIP: not even +0.1R in the first 15 minutes → less likely to work.
    early_cut_enabled: bool = True
    early_cut_min: int = 15
    early_cut_mfe_r: float = 0.1

    # ── Costs (Binance USDT-M VIP0) ──────────────────────────────────────────
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0005                # applied to market entries and stop/time exits

    # ── Get-ready alerts (manual trading) ────────────────────────────────────
    watch_dist_pct: float = 0.004           # alert when price is within 0.4% of a level

    # ── Live runner ──────────────────────────────────────────────────────────
    max_open_positions: int = 5
    history_bars: int = 300                 # 1m bars kept per symbol (needs ≥ 2h + stop search)

    def to_dict(self):
        return asdict(self)


TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
    "1M": 2_678_400_000,        # 31 days — only for request sizing; candles use calendar months
}

# Binance weekly candles open Monday 00:00 UTC; epoch (1 Jan 1970) was a Thursday.
WEEK_OFFSET_MS = 4 * 86_400_000
