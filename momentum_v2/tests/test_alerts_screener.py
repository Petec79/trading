"""Alerts, get-ready watches, watchdog and screener (run: python3 momentum_v2/tests/test_alerts_screener.py)."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for k in ("TELEGRAM_BOT_TOKEN", "TRADING_TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_USERS",
          "DISCORD_SIGNALS_WEBHOOK", "DISCORD_JOURNAL_WEBHOOK"):
    os.environ[k] = ""                        # never hit real endpoints from tests

import test_engine as TE  # noqa: E402
import test_mean_reversion as TM  # noqa: E402
from test_engine import pytest  # noqa: E402
from momentum_v2 import alerts, watchdog, screener  # noqa: E402
from momentum_v2.alerts import Notifier, sizing, rr  # noqa: E402
from momentum_v2.core import Trade, SymbolEngine, Bar  # noqa: E402
from momentum_v2.config import Config  # noqa: E402
from momentum_v2.mean_reversion import MeanReversionEngine, MRConfig  # noqa: E402

NOW = 1_792_400_000_000


class RecNotifier(Notifier):
    """Records messages and hands out fake Telegram message ids."""
    def __init__(self, strategy="mean_reversion"):
        super().__init__(strategy)
        self.n = 100

    def send(self, text, reply_to=None):
        self.sent.append({"text": text, "reply_to": reply_to})
        self.n += 1
        return self.n

    @staticmethod
    def _now(now_ms):                      # tests control the clock
        return now_ms


def mk(side="short", entry_type="market", level=1.2200, stop=1.2410, target=1.1890, ref=1.2195,
       setup="fast_spike", signal_t=NOW):
    return Trade(symbol="FOOUSDT", side=side, level_tf=setup, level=level, signal_t=signal_t,
                 entry_type=entry_type, stop=stop, target=target, target_kind="spike_origin", score=3,
                 criteria={"fast_spike": True, "volume_not_increasing": True, "ranging": True},
                 details={"ref_entry": ref}, setup=setup)


# ── sizing / R:R ─────────────────────────────────────────────────────────────
def test_sizing_10k_at_1pct_and_rr():
    s = sizing(100.0, 101.76)
    assert s["risk_usd"] == 100 and s["notional"] == pytest.approx(100 / 0.0176, rel=1e-6)
    assert rr(100, 101.76, 97.5) == pytest.approx(2.5 / 1.76, rel=1e-6)
    os.environ["ALERT_ACCOUNT"], os.environ["ALERT_RISK_PCT"] = "25000", "0.5"
    try:
        assert sizing(100.0, 99.0)["risk_usd"] == 125
    finally:
        del os.environ["ALERT_ACCOUNT"], os.environ["ALERT_RISK_PCT"]


# ── signal messages ──────────────────────────────────────────────────────────
def test_market_signal_has_order_size_rr_and_validity():
    n = RecNotifier()
    t = mk()
    mid = n.signal(t, NOW + 1500)
    txt = n.sent[-1]["text"]
    assert "MARKET NOW" in txt and "SL 1.2410" in txt and "TP 1.1890" in txt
    assert "R:R 1.42" in txt
    assert "$10,000 @ 1% → risk $100" in txt and "size $5," in txt
    assert "skip if price is already past" in txt and "LATE" not in txt
    assert t.details["tg_msg_id"] == mid


def test_limit_signal_has_cancel_rules():
    n = RecNotifier()
    t = mk(entry_type="limit", level=1.2300, ref=1.2300, target=1.1950)
    n.signal(t, NOW + 1000)
    txt = n.sent[-1]["text"]
    assert "LIMIT SELL at 1.2300" in txt and "Cancel at" in txt and "if price reaches" in txt


def test_late_signals_are_marked():
    n = RecNotifier()
    n.signal(mk(signal_t=NOW - 10 * 60_000), NOW)
    assert n.sent[-1]["text"].startswith("⚠️ LATE")


def test_followups_are_threaded_under_the_signal():
    n = RecNotifier()
    t = mk()
    mid = n.signal(t, NOW)
    t.entry, t.cur_stop = 1.2195, 1.2410
    n.followup("fill", t, NOW + 60_000, NOW + 60_000)
    t.cur_stop = 1.2190
    n.followup("stop_move", t, NOW + 120_000, NOW + 120_000, "+0.9R → SL to +0.05R")
    t.exit, t.exit_reason, t.net_r = 1.2190, "TIME_2h", 0.02
    n.followup("exit", t, NOW + 7_200_000, NOW + 7_200_000)
    assert all(m["reply_to"] == mid for m in n.sent[1:])
    assert "filled" in n.sent[1]["text"] and "move stop to 1.2190" in n.sent[2]["text"]
    assert "EXIT NOW" in n.sent[3]["text"]


def test_news_result_reply():
    n = RecNotifier()
    t = mk()
    n.signal(t, NOW)
    n.news_result(t, {"verdict": "big_news", "items": [{"source": "binance", "title": "Binance Will Delist FOO"}],
                      "hype": {"sample": 50, "posts_per_hour": 120, "bot_share": 0.6}}, "shadow")
    assert "BIG NEWS" in n.sent[-1]["text"] and "60% bot-like" in n.sent[-1]["text"]
    assert n.sent[-1]["reply_to"] == t.details["tg_msg_id"]


# ── get-ready watches ────────────────────────────────────────────────────────
def test_momentum_get_ready_one_close_before_the_trigger():
    c, bars, L, ctx = TE.setup_long()
    pre = bars[:-1]                                  # the candle before the close-through
    assert 0 < (L - pre[-1].c) / pre[-1].c <= c.watch_dist_pct
    e = SymbolEngine("TESTUSDT", c)
    ws = e.watch(pre, ctx)
    assert len(ws) == 1 and ws[0]["side"] == "long" and "CLOSE above" in ws[0]["text"]
    assert e.watch(pre, ctx) == []                   # not repeated
    ev = e.on_bar(bars, ctx)                         # and the real trigger still fires
    assert ev and ev[0]["type"] == "signal"


def test_mr_get_ready_on_spike_and_on_close_outside_range():
    c, bars, ctx, piv = TM.spike_setup()
    spike_end = next(i for i in range(len(bars) - 1, 0, -1) if bars[i].v == 7000)
    e = MeanReversionEngine("TESTUSDT", c)
    ws = e.watch(bars[:spike_end + 1], ctx)
    assert any(w["setup"] == "fast spike reversal" and w["side"] == "short" for w in ws), ws

    c, bars, r_hi = TM.failed_breakout_short()
    outside = bars[:-1]                              # candle that closed above the range
    e = MeanReversionEngine("TESTUSDT", c)
    ws = e.watch(outside, TM.ctx_for(outside))
    assert any(w["setup"] == "failed breakout" and "closes back below" in w["text"] for w in ws), ws


def test_stale_watch_is_not_sent():
    n = RecNotifier()
    n.watch({"symbol": "FOOUSDT", "side": "long", "setup": "x", "text": "y", "bar_close": NOW - 600_000}, NOW)
    assert n.sent == []


# ── watchdog ─────────────────────────────────────────────────────────────────
def test_watchdog_alerts_once_and_on_recovery():
    with tempfile.TemporaryDirectory() as d:
        watchdog.DATA = Path(d)
        (Path(d) / "heartbeat_momentum.json").write_text(json.dumps({"ts": (NOW - 600_000)}))
        n = RecNotifier()
        assert len(watchdog.check(NOW / 1000, n, {"momentum": 180})) == 1
        assert watchdog.check(NOW / 1000, n, {"momentum": 180}) == []          # no repeat
        (Path(d) / "heartbeat_momentum.json").write_text(json.dumps({"ts": NOW}))
        msgs = watchdog.check(NOW / 1000, n, {"momentum": 180})
        assert msgs == ["momentum is running again."]


# ── screener ─────────────────────────────────────────────────────────────────
def test_bias_direction_is_the_guides_not_inverted():
    assert screener.bias_from_returns([1] * 9 + [-1])["bias"] == "BULLISH"
    assert "LONGS" in screener.bias_from_returns([1] * 9 + [-1])["advice"]
    assert "SHORTS" in screener.bias_from_returns([-1] * 9 + [1])["advice"]


def test_screener_classifies_range_and_staircase():
    cfg, mrc = Config(), MRConfig()
    rng = TM.range_bars(400)
    a = screener.analyse("RNGUSDT", rng, TE.FakeCtx(103, 99), cfg, mrc)
    assert "reversal" in a["tags"] and not any(t.startswith("momentum") for t in a["tags"])
    st = TM.stair_bars(400, vol=lambda i: 500 + i * 5)
    b = screener.analyse("STAUSDT", st, TE.FakeCtx(st[-1].c * 1.01, st[0].l), cfg, mrc)
    assert "momentum_long" in b["tags"] and "avoid_reversal_shorts" in b["tags"] and "reversal" not in b["tags"]
    assert b["level_above"]["name"].endswith("high")


def test_screener_build_and_report_with_faked_binance():
    from momentum_v2 import binance, live
    rng, st = TM.range_bars(400), TM.stair_bars(400, vol=lambda i: 500 + i * 5)
    data = {"RNGUSDT": rng, "STAUSDT": st}
    patch = {
        "server_time_ms": lambda: rng[-1].t + 60_000,
        "tradable_alt_perps": lambda majors: set(data),
        "tickers_24h": lambda: [{"symbol": s, "priceChangePercent": "3" if s == "STAUSDT" else "-1",
                                 "quoteVolume": "1e12", "count": 100} for s in data],
        "premium_all": lambda: {"RNGUSDT": (0.001, rng[-1].t + 30 * 60_000)},
        "closed_klines": lambda s, tf, n, now: data[s] if tf == "1m" else [],
    }
    orig = {k: getattr(binance, k) for k in patch}
    orig_rtc = live.recent_trade_counts
    try:
        for k, v in patch.items():
            setattr(binance, k, v)
        live.recent_trade_counts = lambda syms, m, now: {s: 1000 for s in syms}
        rep = screener.build()
        txt = screener.format_report(rep)
    finally:
        for k, v in orig.items():
            setattr(binance, k, v)
        live.recent_trade_counts = orig_rtc
    assert {c["symbol"] for c in rep["coins"]} == set(data)
    assert "MOMENTUM LONG" in txt and "STA" in txt and "REVERSAL-FRIENDLY" in txt and "RNG" in txt
    assert "FUNDING due" in txt and "longs pay" in txt and "MIXED" in txt


# ── regressions from independent review ─────────────────────────────────────
def test_market_skip_tells_you_to_exit_not_cancel():
    n = RecNotifier()
    t = mk(side="long", entry_type="market", level=100.0, ref=100.5, stop=99.0, target=103.0)
    n.signal(t, NOW)
    t.exit_reason = "gapped_through_stop"
    n.followup("cancel", t, NOW + 60_000, NOW + 60_000)
    txt = n.sent[-1]["text"]
    assert "did NOT take" in txt and "EXIT NOW" in txt and "CANCEL" not in txt


def test_limit_cancel_still_says_cancel():
    n = RecNotifier()
    t = mk(entry_type="limit", level=1.23, ref=1.23, target=1.195)
    n.signal(t, NOW)
    t.exit_reason = "limit_expired"
    n.followup("cancel", t, NOW + 60_000, NOW + 60_000)
    assert "CANCEL the short limit order" in n.sent[-1]["text"]


def test_fill_flags_a_stop_rebased_on_the_fill():
    n = RecNotifier()
    t = mk(side="long", entry_type="market", level=100.0, ref=100.0, stop=98.0, target=102.0)
    n.signal(t, NOW)
    t.entry, t.stop, t.cur_stop = 100.6, 99.2, 99.2        # 1R re-based at fill
    n.followup("fill", t, NOW + 60_000, NOW + 60_000)
    assert "STOP CHANGED: now 99.2000 (signal said 98.0000)" in n.sent[-1]["text"]


def test_alerts_use_the_real_send_clock():
    import time as _t
    n = Notifier("momentum")                                 # real _now()
    t = mk(signal_t=int(_t.time() * 1000) - 5 * 60_000)
    n.signal(t, now_ms=t.signal_t + 1000)                   # stale cycle clock says "1s late"
    assert n.sent[-1]["text"].startswith("⚠️ LATE")


def test_watch_and_engine_agree_on_failed_breakout_volume_rule():
    c, bars, r_hi = TM.failed_breakout_short(vol=lambda i: 400 + i * 10)   # rising volume
    e = MeanReversionEngine("TESTUSDT", c)
    assert not any(w["setup"] == "failed breakout" for w in e.watch(bars[:-1], TM.ctx_for(bars[:-1])))
    assert MeanReversionEngine("TESTUSDT", c).on_bar(bars, TM.ctx_for(bars)) == []


def test_momentum_watch_respects_liquidity_filter():
    c, bars, L, ctx = TE.setup_long(TE.cfg(min_quote_vol_per_min=10 ** 12))
    assert SymbolEngine("TESTUSDT", c).watch(bars[:-1], ctx) == []


def test_runner_heartbeat_not_written_without_market_data():
    from momentum_v2 import live, binance
    with tempfile.TemporaryDirectory() as d:
        live.STATE_DIR, live.STATE_FILE = Path(d), Path(d) / "s.json"
        r = live.Runner(journal=False)
        r.allowed = {"FOOUSDT": {"long"}}
        r.universe_t = 10 ** 15
        orig = (binance.server_time_ms, binance.closed_klines)
        try:
            binance.server_time_ms = lambda: NOW

            def boom(*a, **k):
                raise RuntimeError("binance down")
            binance.closed_klines = boom
            r.cycle()
        finally:
            binance.server_time_ms, binance.closed_klines = orig
        assert not (Path(d) / "heartbeat_momentum.json").exists()


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
