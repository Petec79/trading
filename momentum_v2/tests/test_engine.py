"""Tests for Momentum v2 on synthetic candles (run: python3 -m pytest momentum_v2/tests -q)."""
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    import pytest
except ImportError:  # VPS/sandbox without pytest: tiny stand-in, run this file directly
    import math
    import types

    def _approx(v, abs=1e-6, rel=1e-6):
        class A:
            def __eq__(self, o):
                return math.isclose(o, v, rel_tol=rel, abs_tol=abs)
            def __repr__(self):
                return f"approx({v})"
        return A()
    pytest = types.SimpleNamespace(approx=_approx)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from momentum_v2.config import Config  # noqa: E402
from momentum_v2.core import (Bar, LevelContext, SymbolEngine, pivots, staircase,  # noqa: E402
                              slow_grind, volume_increasing, resample, period_start)

T0 = 1_760_000_040_000 - (1_760_000_040_000 % 3_600_000)   # an hour boundary


def path_bars(rets, start_px=100.0, t0=T0, wick=0.0002, vol=lambda i: 2000.0):
    bars, p = [], start_px
    for i, r in enumerate(rets):
        o = p
        p = o * (1 + r)
        v = vol(i)
        bars.append(Bar(t0 + i * 60_000, o, max(o, p) * (1 + wick), min(o, p) * (1 - wick), p, v, v * p))
    return bars


def stair_rets(n, d=1, up=0.001, down=0.0008):
    pat = [up, up, up, -down, -down]
    return [d * pat[i % 5] for i in range(n)]


def chop_rets(n, a=0.0015):
    pat = [a, a, a, -a, -a, -a]
    return [pat[i % 6] for i in range(n)]


class FakeCtx:
    """LevelContext stand-in: fixed previous-1h candle and next levels."""

    def __init__(self, hi, lo, res=None, sup=None):
        self.hi, self.lo, self.res, self.sup = hi, lo, res, sup

    def prev(self, tf, now):
        return Bar(0, 0, self.hi, self.lo, 0, 0) if tf == "1h" else None

    def next_resistance(self, price, now):
        return self.res

    def next_support(self, price, now):
        return self.sup


def cfg(**kw):
    c = Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def setup_long(c=None, res=None, extra_up=0.001):
    """130 bars of rising-volume staircase, then one candle closing through L."""
    c = c or cfg()
    base = path_bars(stair_rets(130), vol=lambda i: 1500 + i * 20)
    L = max(b.h for b in base[-30:]) * 1.0005
    last = base[-1]
    px = L * (1 + extra_up)
    trig = Bar(last.t + 60_000, last.c, px * 1.0002, last.c * 0.9998, px, 4000, 4000 * px)
    bars = base + [trig]
    return c, bars, L, FakeCtx(L, base[0].l * 0.9, res=res)


def nxt(bars, o, h, l, c_, v=3000.0):
    b = Bar(bars[-1].t + 60_000, o, h, l, c_, v, v * c_)
    bars.append(b)
    return b


# ── building blocks ──────────────────────────────────────────────────────────
def test_pivots_three_candle_definition():
    bars = [Bar(i, 1, h, l, 1, 1) for i, (h, l) in enumerate([(1, .5), (3, .4), (2, .6), (2.5, .2), (2.4, .3)])]
    hs, ls = pivots(bars, 1)
    assert hs == [(1, 3), (3, 2.5)]
    assert ls == [(1, .4), (3, .2)]


def test_staircase_detects_trend_not_chop():
    c = Config()
    up = path_bars(stair_rets(150))
    dn = path_bars(stair_rets(150, d=-1))
    ch = path_bars(chop_rets(150))
    assert staircase(up, 1, c)[0] is True
    assert staircase(up, -1, c)[0] is False
    assert staircase(dn, -1, c)[0] is True
    assert staircase(ch, 1, c)[0] is False and staircase(ch, -1, c)[0] is False


def test_staircase_needs_recent_hour_to_trend():
    c = Config()
    bars = path_bars(stair_rets(60) + chop_rets(70))   # old staircase, recent chop (p97-99)
    assert staircase(bars, 1, c)[0] is False


def test_volume_increasing():
    c = Config()
    rising = path_bars(stair_rets(130), vol=lambda i: 1000 + i * 20)
    falling = path_bars(stair_rets(130), vol=lambda i: 4000 - i * 20)
    assert volume_increasing(rising, c)[0] is True
    assert volume_increasing(falling, c)[0] is False


def test_fast_spike_fails_grind():
    c = Config()
    bars = path_bars(stair_rets(130))
    assert slow_grind(bars, 1, c)[0] is True
    last = bars[-1]
    bars.append(Bar(last.t + 60_000, last.c, last.c * 1.03, last.c, last.c * 1.03, 9000, 9000))
    assert slow_grind(bars, 1, c)[0] is False


def test_weekly_candles_align_to_monday():
    monday = 1_792_368_000_000            # 2026-10-19 00:00 UTC, a Monday
    assert period_start(monday + 3 * 86_400_000, "1w") == monday


def test_level_context_no_lookahead():
    c = Config()
    bars = path_bars([0.0005] * 180)          # 3 hours of 1m
    htf = {"1h": resample(bars, "1h")}
    ctx = LevelContext(htf, c)
    second_hour_open = T0 + 3_600_000
    # during hour 2 the "previous 1h" is hour 1, never the still-open hour 2
    assert ctx.prev("1h", second_hour_open + 30 * 60_000).t == T0
    assert ctx.prev("1h", second_hour_open - 1) is None


# ── signals and entries ──────────────────────────────────────────────────────
def test_long_signal_limit_entry_and_take_profit():
    c, bars, L, ctx = setup_long()
    ctx.res = L * 1.0008                    # next level closer than 1R → safe 1R target
    e = SymbolEngine("TESTUSDT", c)
    ev = e.on_bar(bars, ctx)
    assert [x["type"] for x in ev] == ["signal"]
    t = e.trade
    assert t.side == "long" and t.entry_type == "limit" and t.level == L
    assert t.score == 3 and t.target_kind == "1R"
    # retest of the level fills the limit at L
    nxt(bars, bars[-1].c, bars[-1].c, L * 0.9999, L * 1.001)
    ev = e.on_bar(bars, ctx)
    assert any(x["type"] == "fill" for x in ev) and t.entry == pytest.approx(L)
    # rally to target
    tgt = t.target
    nxt(bars, L * 1.001, tgt * 1.001, L * 1.0005, tgt)
    ev = e.on_bar(bars, ctx)
    assert ev[0]["type"] == "exit" and t.exit_reason == "TP"
    assert t.gross_r == pytest.approx(1.0, abs=1e-6)
    assert 0.8 < t.net_r < 1.0                              # maker fees both sides


def test_three_of_three_uses_next_level_target():
    c, bars, L, ctx = setup_long(res=None)
    ctx.res = L * 1.05
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    assert e.trade.target_kind == "next_level" and e.trade.target == pytest.approx(L * 1.05)


def test_no_trade_without_staircase():
    c = cfg()
    base = path_bars(chop_rets(130), vol=lambda i: 1500 + i * 20)
    L = max(b.h for b in base[-30:]) * 1.0005
    px = L * 1.002
    bars = base + [Bar(base[-1].t + 60_000, base[-1].c, px, base[-1].c, px, 4000, 4000 * px)]
    e = SymbolEngine("TESTUSDT", c)
    assert e.on_bar(bars, FakeCtx(L, 50)) == []


def test_no_entry_until_candle_closes_through():
    c, bars, L, ctx = setup_long()
    # same candle but closing back below the level (wick through only)
    last = bars[-1]
    bars[-1] = Bar(last.t, last.o, last.h, last.l, L * 0.9995, last.v, last.qv)
    e = SymbolEngine("TESTUSDT", c)
    assert e.on_bar(bars, ctx) == []


def test_market_entry_when_stop_wider_than_3pct():
    c, bars, L, ctx = setup_long(cfg(min_stop_pct=0.03))
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    assert t is not None and t.entry_type == "market"
    o = bars[-1].c
    nxt(bars, o, o * 1.001, o * 0.999, o)
    ev = e.on_bar(bars, ctx)
    assert ev[0]["type"] == "fill" and t.entry == pytest.approx(o * (1 + c.slippage))


def test_limit_cancelled_if_price_runs_to_target():
    c, bars, L, ctx = setup_long()
    ctx.res = L * 1.0008
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    nxt(bars, bars[-1].c, t.target * 1.01, L * 1.001, t.target)
    ev = e.on_bar(bars, ctx)
    assert ev[0]["type"] == "cancel" and t.exit_reason == "missed_ran_to_target"
    assert e.trade is None


def test_one_attempt_per_level():
    c, bars, L, ctx = setup_long()
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    e.trade = None                       # pretend it was cancelled
    nxt(bars, L, L, L * 0.995, L * 0.996)     # back below
    e.on_bar(bars, ctx)
    nxt(bars, L * 0.996, L * 1.003, L * 0.996, L * 1.002)   # closes through again
    assert e.on_bar(bars, ctx) == []


# ── trade management ────────────────────────────────────────────────────────
def filled_long(**kw):
    c, bars, L, ctx = setup_long(cfg(**kw))
    ctx.res = L * 1.0008
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    nxt(bars, bars[-1].c, bars[-1].c, L * 0.9999, L * 1.0005)
    e.on_bar(bars, ctx)
    assert e.trade.status == "open"
    return e, bars, ctx, e.trade


def test_stop_wins_when_stop_and_target_share_a_candle():
    e, bars, ctx, t = filled_long()
    nxt(bars, t.entry, t.target * 1.01, t.stop * 0.99, t.entry)
    e.on_bar(bars, ctx)
    assert t.exit_reason == "SL" and t.net_r < -1.0


def test_evolving_r_rule_moves_stop_to_plus_0_1R():
    e, bars, ctx, t = filled_long(early_cut_enabled=False)
    up = t.entry + 0.95 * t.risk
    nxt(bars, t.entry, up, t.entry, up)
    ev = e.on_bar(bars, ctx)
    assert any(x["type"] == "stop_move" for x in ev)
    assert t.cur_stop == pytest.approx(t.entry + 0.1 * t.risk)
    nxt(bars, up, up, t.entry * 0.999, t.entry)
    e.on_bar(bars, ctx)
    assert t.exit_reason == "SL_after_+0.9R"
    expected = 0.1 - Config().slippage * t.cur_stop / t.risk     # stop exit pays slippage
    assert t.gross_r == pytest.approx(expected, abs=1e-3)


def test_time_stop_at_two_hours():
    e, bars, ctx, t = filled_long(early_cut_enabled=False)
    px = t.entry + 0.3 * t.risk
    for _ in range(125):
        if t.status != "open":
            break
        nxt(bars, px, px * 1.0001, px * 0.9999, px)
        e.on_bar(bars, ctx)
    assert t.exit_reason == "TIME_2h"
    assert (t.exit_t - t.entry_t) / 60_000 == pytest.approx(120, abs=1)


def test_early_cut_when_nothing_happens_in_15_minutes():
    e, bars, ctx, t = filled_long()
    px = t.entry
    for _ in range(20):
        if t.status != "open":
            break
        nxt(bars, px, px * 1.00001, px * 0.99999, px)
        e.on_bar(bars, ctx)
    assert t.exit_reason == "EARLY_CUT_15m"


def test_trail_mode_when_no_level_above():
    c, bars, L, ctx = setup_long()
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    assert t.target is None and t.target_kind == "trail"


# ── shorts ───────────────────────────────────────────────────────────────────
def test_short_mirror():
    c = cfg(early_cut_enabled=False)
    base = path_bars(stair_rets(130, d=-1), vol=lambda i: 1500 + i * 20)
    L = min(b.l for b in base[-30:]) * 0.9995
    px = L * 0.999
    bars = base + [Bar(base[-1].t + 60_000, base[-1].c, base[-1].c, px * 0.9998, px, 4000, 4000 * px)]
    ctx = FakeCtx(base[0].h * 1.1, L, sup=None)
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    assert t.side == "short" and t.stop > L and t.target_kind == "trail"
    nxt(bars, px, L * 1.0001, px, L * 0.999)
    e.on_bar(bars, ctx)
    assert t.status == "open" and t.entry == pytest.approx(L)
    nxt(bars, t.entry, t.stop * 1.01, t.entry, t.stop)
    e.on_bar(bars, ctx)
    assert t.exit_reason == "SL" and t.net_r < -1.0


# ── journal ──────────────────────────────────────────────────────────────────
def test_journal_records_real_r_and_legacy_exit_check_skips_v2():
    from momentum_v2.reporting import JournalBridge
    from journal.journal import Journal
    e, bars, ctx, t = filled_long()
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "j.db"
        jb = JournalBridge(str(db))
        jb.on_fill(t)
        # the old hourly exit monitor must not touch momentum_v2 trades
        closed = Journal(str(db)).check_pending_exits(
            {"TESTUSDT": {"price": t.stop * 0.5, "direction": "SHORT"}})
        assert closed == []
        nxt(bars, t.entry, t.target * 1.001, t.entry, t.target)
        e.on_bar(bars, ctx)
        jb.on_exit(t)
        row = sqlite3.connect(db).execute(
            "select scanner, outcome, r_multiple, entry_type from trades").fetchone()
        assert row[0] == "momentum_v2" and row[1] == "win"
        assert row[2] == pytest.approx(t.net_r, abs=1e-3) and row[3] == "limit"


# ── regressions from independent review ─────────────────────────────────────
def test_level_is_period_closed_before_trigger_candle_opened():
    c, bars, L, ctx = setup_long()
    asked = []
    orig = ctx.prev
    ctx.prev = lambda tf, t: (asked.append(t), orig(tf, t))[1]
    SymbolEngine("TESTUSDT", c).on_bar(bars, ctx)
    assert asked and all(t == bars[-1].t for t in asked)


def test_market_fill_revalidates_level_target():
    c, bars, L, ctx = setup_long(cfg(min_stop_pct=0.03))
    ctx.res = bars[-1].c * 1.10
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    assert t.entry_type == "market" and t.target_kind == "next_level"
    # next open gaps up past the level target → target must fall back to 1R above the fill
    o = ctx.res * 1.01
    nxt(bars, o, o * 1.0001, o * 0.9999, o)
    e.on_bar(bars, ctx)
    assert t.target_kind == "1R" and t.target > t.entry


def test_target_level_must_be_beyond_trigger_close():
    c, bars, L, ctx = setup_long(cfg(min_stop_pct=0.03))
    # a "next resistance" between the level and the close has already been broken
    ctx.next_resistance = lambda price, now: (bars[-1].c * 1.2 if price >= bars[-1].c else L * 1.0001)
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    assert e.trade.target == pytest.approx(bars[-1].c * 1.2)


def test_limit_gap_through_stop_is_cancelled_not_filled():
    c, bars, L, ctx = setup_long()
    e = SymbolEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    t = e.trade
    o = t.stop * 0.99
    nxt(bars, o, o, o * 0.999, o)
    ev = e.on_bar(bars, ctx)
    assert ev[0]["type"] == "cancel" and t.exit_reason == "gapped_through_stop"


def test_live_weekly_cache_refreshes_on_monday():
    from momentum_v2 import live
    from momentum_v2.core import period_start
    monday = 1_792_368_000_000
    assert period_start(monday, "1w") != period_start(monday - 1, "1w")
    assert "period_start(now_ms, tf)" in Path(live.__file__).read_text()


def test_hot_coins_ranked_by_last_5_minute_trades():
    from momentum_v2 import live, binance
    with tempfile.TemporaryDirectory() as d:
        live.STATE_FILE = Path(d) / "s.json"
        r = live.Runner(Config(), journal=False)
        tick = [{"symbol": f"C{i}USDT", "count": 1000 - i} for i in range(20)]   # 24h order: C0..C19
        recent = {f"C{i}USDT": i * 10 for i in range(20)}                          # 5m order: C19..C0
        orig = binance.trades_last_minutes
        try:
            binance.trades_last_minutes = lambda s, m, now: recent[s]
            hot = r.hot_by_recent_trades(tick, 0)
            assert hot[:3] == ["C19USDT", "C18USDT", "C17USDT"] and len(hot) == 10

            def boom(*a):
                raise RuntimeError("down")
            binance.trades_last_minutes = boom
            assert r.hot_by_recent_trades(tick, 0)[:2] == ["C0USDT", "C1USDT"]   # falls back to 24h
        finally:
            binance.trades_last_minutes = orig


if __name__ == "__main__":
    import traceback
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("PASS", n)
        except Exception:
            fails += 1
            print("FAIL", n)
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
