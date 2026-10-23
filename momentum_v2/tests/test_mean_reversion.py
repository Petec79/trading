"""Mean reversion v2 tests on synthetic candles (run: python3 momentum_v2/tests/test_mean_reversion.py)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine import FakeCtx, pytest, T0  # noqa: E402  (shared helpers / pytest shim)
from momentum_v2.core import Bar  # noqa: E402
from momentum_v2.mean_reversion import (MRConfig, MeanReversionEngine, BTCSeries,  # noqa: E402
                                        find_spike, volume_not_increasing, is_ranging,
                                        range_duration_hours, funding_blocks)


def cfg(**kw):
    c = MRConfig()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def range_bars(n=280, lo=100.0, hi=102.0, period=12, vol=lambda i: 1000.0, t0=T0):
    """Triangle wave between lo and hi — a textbook sideways range."""
    bars, half = [], period // 2
    prev = lo
    for i in range(n):
        ph = i % period
        frac = ph / half if ph < half else (period - ph) / half
        c = lo + (hi - lo) * frac
        o = prev
        v = vol(i)
        bars.append(Bar(t0 + i * 60_000, o, max(o, c) * 1.0002, min(o, c) * 0.9998, c, v, v * c * 200))
        prev = c
    return bars


def stair_bars(n=280, start=100.0, d=1, vol=lambda i: 1000.0):
    pat = [0.0012, 0.0012, 0.0012, -0.0006, -0.0006]
    bars, p = [], start
    for i in range(n):
        o = p
        p = o * (1 + d * pat[i % 5])
        v = vol(i)
        bars.append(Bar(T0 + i * 60_000, o, max(o, p) * 1.0002, min(o, p) * 0.9998, p, v, v * p * 200))
    return bars


def add(bars, o, h, l, c, v=1000.0):
    b = Bar(bars[-1].t + 60_000, o, h, l, c, v, v * c * 200)
    bars.append(b)
    return b


def run(eng, bars, ctx):
    return eng.on_bar(bars, ctx)


def ctx_for(bars, res=None, sup=None, hi=None, lo=None):
    return FakeCtx(hi if hi is not None else max(b.h for b in bars) * 1.5,
                   lo if lo is not None else min(b.l for b in bars) * 0.5, res=res, sup=sup)


def ending_at_top(bars):
    """Trim so the series ends right as the triangle wave tops out."""
    while not (bars[-1].c >= bars[-2].c and bars[-1].c >= 101.99):
        bars.pop()
    return bars


# ── regime variables ─────────────────────────────────────────────────────────
def test_range_detected_and_staircase_not():
    c = MRConfig()
    assert is_ranging(range_bars(130)[-120:], -1, c)[0] is True
    assert is_ranging(stair_bars(130)[-120:], -1, c)[0] is False


def test_choppy_trend_in_trade_direction_counts_as_ranging():
    c = MRConfig()
    # a choppy downtrend (lots of MA crosses, drifting lower) is fine for reversal SHORTS (P1 p84)
    bars = range_bars(130)
    drift = [Bar(b.t, b.o * (1 - i * 0.0002), b.h * (1 - i * 0.0002), b.l * (1 - i * 0.0002),
                 b.c * (1 - i * 0.0002), b.v, b.qv) for i, b in enumerate(bars)]
    ok_short, d = is_ranging(drift[-120:], -1, c)
    assert ok_short is True


def test_volume_flat_vs_increasing():
    c = MRConfig()
    assert volume_not_increasing(range_bars(280), c)[0] is True
    assert volume_not_increasing(range_bars(280, vol=lambda i: 500 + i * 10), c)[0] is False


def test_range_duration_hours():
    c = MRConfig()
    assert range_duration_hours(range_bars(300), c) >= 4
    assert range_duration_hours(range_bars(150) + stair_bars(150, start=102), c) < 2


def test_fast_spike_detection_needs_size_slope_and_volume():
    c = MRConfig()
    bars = range_bars(260, lo=100, hi=100.5)
    for px, v in ((101.2, 6000), (102.0, 6000), (102.8, 6000)):
        add(bars, bars[-1].c, px, bars[-1].c * 0.999, px, v)
    sp = find_spike(bars, len(bars) - 1, 1, c)
    assert sp and sp["size_pct"] > 2 and sp["bars"] <= 5
    quiet = range_bars(260, lo=100, hi=100.5)
    for px in (101.2, 102.0, 102.8):
        add(quiet, quiet[-1].c, px, quiet[-1].c * 0.999, px, 1000)      # no volume spike
    assert find_spike(quiet, len(quiet) - 1, 1, c) is None


def test_funding_rule():
    now = 1_000_000
    assert funding_blocks(0.25, now + 30 * 60_000, now, stop_dist_pct=2.27) is True    # > 0.227 within 90 min
    assert funding_blocks(0.20, now + 30 * 60_000, now, stop_dist_pct=2.27) is False
    assert funding_blocks(0.25, now + 120 * 60_000, now, stop_dist_pct=2.27) is False


# ── A. failed breakout ───────────────────────────────────────────────────────
def failed_breakout_short(vol=lambda i: 1000.0, base=None, c=None):
    c = c or cfg()
    bars = base or ending_at_top(range_bars(280, vol=vol))
    r_hi = max(b.h for b in bars[-120:])
    add(bars, bars[-1].c, r_hi * 1.004, bars[-1].c, r_hi * 1.003)         # closes above the range
    add(bars, bars[-1].c, bars[-1].c * 1.0005, r_hi * 0.997, r_hi * 0.998)  # closes back inside
    return c, bars, r_hi


def test_failed_breakout_short_limit_on_level_targets_range_low():
    c, bars, r_hi = failed_breakout_short()
    e = MeanReversionEngine("TESTUSDT", c)
    ev = run(e, bars, ctx_for(bars))
    assert [x["type"] for x in ev] == ["signal"], ev
    t = e.trade
    assert t.setup == "failed_breakout" and t.side == "short"
    assert t.entry_type == "limit" and t.level == pytest.approx(r_hi)      # range ~2% < 3%
    assert t.target == pytest.approx(min(b.l for b in bars[-122:-2]))
    assert t.stop > max(b.h for b in bars[-2:])                            # beyond the failed wick
    assert t.criteria["ranging"] and t.criteria["volume_not_increasing"]


def test_failed_breakout_uses_next_level_as_stop_when_roughly_1R():
    c, bars, r_hi = failed_breakout_short()
    dist = r_hi - min(b.l for b in bars[-122:-2])
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx_for(bars, res=r_hi + dist))
    assert e.trade.stop == pytest.approx(r_hi + dist) and e.trade.stop_kind == "level"


def test_failed_breakout_refused_when_volume_increasing():
    c, bars, _ = failed_breakout_short(vol=lambda i: 400 + i * 10)
    e = MeanReversionEngine("TESTUSDT", c)
    assert run(e, bars, ctx_for(bars)) == []


def test_no_reversal_short_into_an_up_staircase():
    c = cfg()
    bars = stair_bars(280)
    r_hi = max(b.h for b in bars[-120:])
    add(bars, bars[-1].c, r_hi * 1.004, bars[-1].c, r_hi * 1.003)
    add(bars, bars[-1].c, bars[-1].c, r_hi * 0.997, r_hi * 0.998)
    e = MeanReversionEngine("TESTUSDT", c)
    assert run(e, bars, ctx_for(bars)) == []


def test_sfp_single_candle_wick_counts_as_failed_breakout():
    c = cfg()
    bars = ending_at_top(range_bars(280))
    r_hi = max(b.h for b in bars[-120:])
    add(bars, bars[-1].c, r_hi * 1.004, r_hi * 0.996, r_hi * 0.997)      # wick above, close inside
    e = MeanReversionEngine("TESTUSDT", c)
    ev = run(e, bars, ctx_for(bars))
    assert ev and e.trade.setup == "failed_breakout"


def test_limit_cancelled_at_90pct_of_the_way_to_target():
    c, bars, r_hi = failed_breakout_short()
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx_for(bars))
    t = e.trade
    trip = t.level + 0.9 * (t.target - t.level)
    add(bars, bars[-1].c, bars[-1].c, trip * 0.999, trip * 0.9995)
    ev = run(e, bars, ctx_for(bars))
    assert ev[0]["type"] == "cancel" and t.exit_reason == "missed_ran_to_target"


def test_limit_cancelled_after_60_minutes():
    c, bars, r_hi = failed_breakout_short()
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx_for(bars))
    t = e.trade
    px = bars[-1].c
    for _ in range(60):
        if t.status != "pending":
            break
        add(bars, px, px * 1.0001, px * 0.9999, px)
        run(e, bars, ctx_for(bars))
    assert t.status == "cancelled" and t.exit_reason == "limit_expired"


def test_full_trade_fill_and_take_profit_net_of_fees():
    c, bars, r_hi = failed_breakout_short()
    e = MeanReversionEngine("TESTUSDT", c)
    ctx = ctx_for(bars)
    run(e, bars, ctx)
    t = e.trade
    add(bars, bars[-1].c, r_hi * 1.0001, bars[-1].c, r_hi * 0.999)        # retest fills the limit
    run(e, bars, ctx)
    assert t.status == "open" and t.entry == pytest.approx(r_hi)
    add(bars, t.entry, t.entry, t.target * 0.999, t.target)
    run(e, bars, ctx)
    assert t.exit_reason == "TP" and t.gross_r > 0.5 and t.net_r < t.gross_r


# ── B. fast spike reversal ───────────────────────────────────────────────────
def spike_setup(btc_spike=False, size=(101.6, 102.4, 103.2)):
    c = cfg()
    bars = range_bars(262, lo=100.0, hi=100.8)
    while bars[-1].c > 100.2:
        bars.pop()
    for px in size:                                                    # vertical, big volume
        add(bars, bars[-1].c, px, bars[-1].c * 0.9995, px * 0.9995, 7000)
    top = bars[-1].h
    add(bars, bars[-1].c, top * 0.9995, 102.3, 102.4, 3000)            # back under the 103 level
    add(bars, 102.4, 102.6, 101.9, 102.0, 2000)                        # swing low forms at ~101.9
    add(bars, 102.0, 102.5, 101.95, 102.4, 1500)
    add(bars, 102.4, 102.45, 102.1, 102.2, 1200)
    ctx = ctx_for(bars, hi=103.0)                                      # previous 1h high = 103
    if btc_spike:
        btc = [Bar(b.t, 60000, 60000, 60000, 60000, 1) for b in bars]
        for b in btc[-8:-4]:
            b.o, b.h, b.c = 60000, 61000, 61000
        ctx.btc = BTCSeries(btc)
    else:
        ctx.btc = BTCSeries([Bar(b.t, 60000, 60010, 59990, 60000, 1) for b in bars])
    piv = 101.9 * 0.9998
    return c, bars, ctx, piv


def test_fast_spike_reversal_on_break_of_structure():
    c, bars, ctx, piv = spike_setup()
    e = MeanReversionEngine("TESTUSDT", c)
    assert run(e, bars, ctx) == []                                     # no break of structure yet
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)                       # closes below the swing low
    ev = run(e, bars, ctx)
    assert ev and e.trade.setup == "fast_spike" and e.trade.side == "short", ev
    t = e.trade
    assert t.entry_type == "market"                                    # spike > 1.5%
    assert t.target == pytest.approx(t.details["spike"]["origin"])
    assert t.stop > t.details["spike"]["extreme"] or t.stop_kind == "1R"
    assert t.details["bos_quality"] == "good"


def test_fast_spike_skipped_when_btc_spiked_too():
    c, bars, ctx, piv = spike_setup(btc_spike=True)
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx)
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)
    ev = run(e, bars, ctx)
    assert not any(x["type"] == "signal" and x["trade"].setup == "fast_spike" for x in ev)


def test_fast_spike_skipped_when_no_key_level_was_hit():
    c, bars, ctx, piv = spike_setup()
    ctx.hi = 110.0                                                     # nearest level far above the spike
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx)
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)
    ev = run(e, bars, ctx)
    assert not any(x["type"] == "signal" and x["trade"].setup == "fast_spike" for x in ev)


# ── C. partial fill / top tick ───────────────────────────────────────────────
def test_top_tick_exact_price_then_red_candle_shorts_at_market():
    c = cfg()
    bars = range_bars(280, lo=100.0, hi=102.0)
    # make one earlier swing high exactly 102.5 (a relevant level)
    k = len(bars) - 60
    while not (bars[k].c >= bars[k - 1].c and bars[k].c >= bars[k + 1].c):
        k += 1
    bars[k] = Bar(bars[k].t, bars[k].o, 102.5, bars[k].l, bars[k].c, bars[k].v, bars[k].qv)
    bars = ending_at_top(bars[:])
    add(bars, bars[-1].c, 102.5, bars[-1].c * 0.999, 102.3, 4000)      # top tick: identical high, big volume
    add(bars, 102.3, 102.35, 101.9, 102.0, 1500)                       # next candle closes red
    e = MeanReversionEngine("TESTUSDT", c)
    ev = run(e, bars, ctx_for(bars))
    sig = [x for x in ev if x["type"] == "signal"]
    assert sig and sig[0]["trade"].setup == "partial_fill", ev
    t = sig[0]["trade"]
    assert t.side == "short" and t.entry_type == "market" and t.level == 102.5
    assert t.target < 102.0 * 0.99 + 1e-9 or (102.0 - t.target) / 102.0 >= 0.01


def test_near_miss_top_tick_is_not_traded():
    c = cfg()
    bars = range_bars(280, lo=100.0, hi=102.0)
    k = len(bars) - 60
    while not (bars[k].c >= bars[k - 1].c and bars[k].c >= bars[k + 1].c):
        k += 1
    bars[k] = Bar(bars[k].t, bars[k].o, 102.5, bars[k].l, bars[k].c, bars[k].v, bars[k].qv)
    bars = ending_at_top(bars[:])
    add(bars, bars[-1].c, 102.49, bars[-1].c * 0.999, 102.3, 4000)     # one tick short: NOT valid (P1 p137)
    add(bars, 102.3, 102.35, 101.9, 102.0, 1500)
    e = MeanReversionEngine("TESTUSDT", c)
    ev = run(e, bars, ctx_for(bars))
    assert not any(x["type"] == "signal" and x["trade"].setup == "partial_fill" for x in ev)


# ── management ───────────────────────────────────────────────────────────────
def test_mr_management_rules_from_part_2():
    c = MRConfig()
    assert c.evolve_trigger_r == 0.9 and 0 <= c.evolve_stop_r <= 0.1
    assert c.time_stop_min == 120 and c.early_cut_enabled is False
    assert c.min_target_pct == 0.01 and c.missed_fraction == 0.9 and c.limit_expiry_bars == 60


def test_1R_stop_is_rebased_on_the_market_fill():
    c, bars, ctx, piv = spike_setup()
    e = MeanReversionEngine("TESTUSDT", c)
    run(e, bars, ctx)
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)
    run(e, bars, ctx)
    t = e.trade
    t.stop_kind = "1R"
    o = 101.55
    add(bars, o, o * 1.0005, o * 0.9995, o)
    run(e, bars, ctx)
    assert t.status == "open"
    assert (t.stop - t.entry) == pytest.approx(t.entry - t.target, abs=1e-6)


# ── regressions from independent review ─────────────────────────────────────
def test_spike_volume_does_not_count_against_volume_check():
    c, bars, ctx, piv = spike_setup()
    for j in range(20):                                   # chop for 20 min before structure breaks
        hi = 102.5 if j % 2 else 102.3
        add(bars, bars[-1].c, hi, 102.1, hi - 0.1, 1000)
    add(bars, bars[-1].c, 102.3, 102.0, 102.2, 1000)
    add(bars, 102.2, 102.35, 102.1, 102.3, 1000)
    e = MeanReversionEngine("TESTUSDT", c)
    for k in range(len(bars) - 2, len(bars) + 1):
        e.on_bar(bars[:k], ctx)
    add(bars, 102.3, 102.3, 101.5, 101.6, 1500)
    e.on_bar(bars, ctx)
    assert e.trade and e.trade.setup == "fast_spike" and e.trade.criteria["volume_not_increasing"]


def test_btc_veto_works_for_a_one_candle_spike():
    c = cfg()
    bars = range_bars(262, lo=100.0, hi=100.8)
    while bars[-1].c > 100.2:
        bars.pop()
    add(bars, bars[-1].c, 103.2, min(b.l for b in bars[-3:]) * 0.999, 103.0, 20000)
    e_i = len(bars) - 1
    for o, h, l, cl, v in ((103.0, 103.0, 102.3, 102.4, 3000), (102.4, 102.6, 101.9, 102.0, 2000),
                           (102.0, 102.5, 101.95, 102.4, 1500), (102.4, 102.45, 102.1, 102.2, 1200)):
        add(bars, o, h, l, cl, v)
    ctx = ctx_for(bars, hi=103.0)
    btc = [Bar(b.t, 60000, 60010, 59990, 60000, 1) for b in bars]
    btc[e_i] = Bar(bars[e_i].t, 60000, 61300, 60000, 61200, 1)        # BTC +2% in the same minute
    ctx.btc = BTCSeries(btc)
    eng = MeanReversionEngine("TESTUSDT", c)
    eng.on_bar(bars, ctx)
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)
    eng.on_bar(bars, ctx)
    assert eng.trade is None


def test_spike_that_stops_short_of_an_older_high_is_still_traded():
    c, bars, ctx, piv = spike_setup()
    j = len(bars) - 60
    b = bars[j]
    bars[j] = Bar(b.t, b.o, 103.25, b.l, b.c, b.v, b.qv)                # older wick set the level
    ctx.hi = 103.25
    e = MeanReversionEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    add(bars, 102.2, 102.25, 101.5, 101.6, 1500)
    e.on_bar(bars, ctx)
    assert e.trade and e.trade.setup == "fast_spike"


def test_funding_only_blocks_the_side_that_pays():
    now = 0
    assert funding_blocks(0.5, now + 10 * 60_000, now, 2.0, "long") is True     # longs pay positive funding
    assert funding_blocks(0.5, now + 10 * 60_000, now, 2.0, "short") is False   # shorts receive it
    assert funding_blocks(-0.5, now + 10 * 60_000, now, 2.0, "short") is True


def test_failed_breakout_needs_edges_that_held():
    c = cfg()
    bars = ending_at_top(range_bars(280))
    # one fresh 2h high that was never tested before → not a range edge that "held"
    j = len(bars) - 30
    b = bars[j]
    bars[j] = Bar(b.t, b.o, 103.0, b.l, b.c, b.v, b.qv)
    add(bars, bars[-1].c, 103.2, bars[-1].c, 103.1)
    add(bars, 103.1, 103.1, 102.6, 102.7)
    e = MeanReversionEngine("TESTUSDT", c)
    ev = e.on_bar(bars, ctx_for(bars))
    assert not any(x["type"] == "signal" and x["trade"].setup == "failed_breakout" for x in ev)


def small_spike_setup(pierce=True):
    c = cfg()
    bars = range_bars(262, lo=100.0, hi=100.4)
    while bars[-1].c > 100.1:
        bars.pop()
    # ~1.2-1.3% spike. pierce: level 101.0, spike tops 101.25 (failed break).
    # no pierce: level 101.4, spike tops 101.33 — reaches the level (within 0.1%) but never trades above it.
    top, level = (101.25, 101.0) if pierce else (101.33, 101.4)
    for px in (100.5, 100.9, top):
        add(bars, bars[-1].c, px, bars[-1].c * 0.9998, px * 0.9998, 7000)
    ctx = ctx_for(bars, hi=level)
    ctx.btc = BTCSeries([Bar(b.t, 60000, 60010, 59990, 60000, 1) for b in bars])
    return c, bars, ctx


def test_small_spike_enters_on_retest_of_the_failed_level():
    c, bars, ctx = small_spike_setup()
    e = MeanReversionEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)                                   # still above the level
    add(bars, bars[-1].c, bars[-1].c, 100.8, 100.85, 2500)  # first close back under 101.0
    ev = e.on_bar(bars, ctx)
    t = e.trade
    assert t and t.setup == "fast_spike" and t.details["entry_style"] == "retest", ev
    assert t.entry_type == "limit" and t.level == pytest.approx(101.0)
    assert t.stop > 101.25 or t.stop_kind == "1R"
    assert t.target == pytest.approx(t.details["spike"]["origin"])


def test_small_spike_that_never_pierced_the_level_is_not_a_retest_trade():
    c, bars, ctx = small_spike_setup(pierce=False)
    from momentum_v2.mean_reversion import find_spike
    sp = find_spike(bars, len(bars) - 1, 1, c)
    assert sp and sp["size_pct"] <= 1.5                                # it IS a small spike…
    probe = MeanReversionEngine("X", c)
    assert probe._key_level_hit(ctx, 1, sp["origin"], bars[-1].h, bars[-1].t) == 101.4   # …into a key level
    e = MeanReversionEngine("TESTUSDT", c)
    e.on_bar(bars, ctx)
    add(bars, bars[-1].c, bars[-1].c, 100.6, 100.65, 2500)
    ev = e.on_bar(bars, ctx)
    assert not any(x["type"] == "signal" and x["trade"].setup == "fast_spike" for x in ev)


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
