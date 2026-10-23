"""News filter tests — all network sources faked (run: python3 momentum_v2/tests/test_news.py)."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine import pytest  # noqa: E402,F401
from momentum_v2 import news  # noqa: E402
from momentum_v2.news import NewsChecker, NewsItem, blocks, classify, mentions, hype_score, bot_like  # noqa: E402

NOW = 1_792_400_000_000
MIN = 60_000


def fake_sources(monkey: dict):
    """Patch every fetcher. monkey: name -> list of items, or an Exception to raise."""
    def mk(name):
        def f(*a, **k):
            v = monkey.get(name, [])
            if isinstance(v, Exception):
                raise v
            return v
        return f
    news.bybit_announcements = mk("bybit")
    news.okx_announcements = mk("okx")
    news.binance_announcements = mk("binance")
    news.rss_items = lambda url: mk("rss")()
    news.apify_search = lambda q, n, actor, token: mk("x_allow" if "from:" in q else "x_sample")()


def checker():
    c = NewsChecker()
    c.sources = {"rss": ["https://example.com/rss"], "x_allowlist": ["binance", "PeckShieldAlert"],
                 "coin_accounts": {}}
    return c


def test_ticker_matching():
    assert mentions("Binance Will List Pepe (PEPE) with Seed Tag", "PEPE")
    assert mentions("$WIF pumps after listing", "WIF")
    assert mentions("New perpetual: ARCUSDT", "ARC")
    assert not mentions("Bitcoin hits new high", "ARC")
    assert not mentions("the op-ed says", "OP")              # 2-letter tickers need $OP / (OP)
    assert mentions("Optimism ($OP) upgrade", "OP")


def test_classification_uses_the_guides_news_types():
    assert "delisting" in classify("Binance Will Delist XYZ, ABC on 2026-11-01")
    assert "hack" in classify("XYZ protocol exploited, $4m drained")
    assert "listing" in classify("Bybit will list XYZ perpetual contract")
    assert "partnership" in classify("XYZ announces partnership with Google Cloud")
    assert classify("XYZ price is up 20% today") == []


def test_exchange_listing_in_window_is_big_news():
    fake_sources({"binance": [NewsItem("binance", "Binance Will List Foo (FOO)", NOW - 30 * MIN)]})
    os.environ.pop("APIFY_TOKEN", None)
    r = checker().check("FOOUSDT", NOW)
    assert r["verdict"] == "big_news" and r["items"][0]["kinds"] == ["listing"]
    assert blocks(r, "enforce") and not blocks(r, "shadow")


def test_old_or_irrelevant_items_are_ignored():
    fake_sources({"binance": [NewsItem("binance", "Binance Will List Foo (FOO)", NOW - 10 * 60 * MIN)],
                  "rss": [NewsItem("rss", "FOO price up 30% in an hour", NOW - 5 * MIN)]})
    os.environ.pop("APIFY_TOKEN", None)
    r = checker().check("FOOUSDT", NOW)
    assert r["verdict"] == "no_news"


def test_all_sources_down_is_check_failed_and_blocks_in_enforce():
    boom = RuntimeError("timeout")
    fake_sources({"bybit": boom, "okx": boom, "binance": boom, "rss": boom})
    os.environ.pop("APIFY_TOKEN", None)
    r = checker().check("FOOUSDT", NOW)
    assert r["verdict"] == "check_failed" and blocks(r, "enforce")


def test_allowlisted_x_post_counts_and_hype_is_recorded():
    def tw(text, mins_ago, followers=50_000, user="PeckShieldAlert"):
        ts = (NOW - mins_ago * MIN) / 1000
        from datetime import datetime, timezone
        return {"text": text, "createdAt": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a %b %d %H:%M:%S %z %Y"),
                "author": {"userName": user, "followers": followers, "following": 10}}
    bots = [tw(f"$FOO 100x gem 🚀 join t.me/pumpgroup $BAR $BAZ $QUX #{i}", i, followers=12) for i in range(30)]
    humans = [tw("watching $FOO here", i, followers=5000) for i in range(10)]
    fake_sources({"x_allow": [tw("#PeckShieldAlert $FOO exploited, ~$2m drained", 20)],
                  "x_sample": bots + humans})
    os.environ["APIFY_TOKEN"] = "test"
    try:
        r = checker().check("FOOUSDT", NOW)
    finally:
        os.environ.pop("APIFY_TOKEN", None)
    assert r["verdict"] == "big_news" and r["items"][0]["source"] == "x"
    assert r["hype"]["bot_share"] >= 0.7 and r["hype"]["sample"] == 40


def test_bot_heuristics():
    assert bot_like({"text": "$A $B $C $D moon", "author": {"followers": 20, "following": 3000}})
    assert not bot_like({"text": "Interesting $FOO chart", "author": {"followers": 20000, "following": 300}})
    assert hype_score([], NOW)["posts_per_hour"] is None


def test_live_runner_shadow_records_and_enforce_blocks():
    from momentum_v2 import live
    from momentum_v2.core import Trade
    from momentum_v2.mean_reversion import MeanReversionEngine

    class FakeNews:
        def __init__(self, verdict):
            self.verdict = verdict

        def check(self, *a):
            return {"verdict": self.verdict, "items": [], "hype": None, "errors": []}

    def mk_trade():
        return Trade(symbol="FOOUSDT", side="short", level_tf="fast_spike", level=1.0, signal_t=NOW,
                     entry_type="market", stop=1.05, target=0.95, target_kind="spike_origin", score=3,
                     criteria={}, details={}, setup="fast_spike")
    with tempfile.TemporaryDirectory() as d:
        live.STATE_DIR = Path(d)
        r = live.Runner(journal=False, strategy="mean_reversion")
        live.binance.premium_index = lambda s: (0.0, None)
        for mode, verdict, kept in (("shadow", "big_news", True), ("enforce", "big_news", False),
                                    ("enforce", "no_news", True), ("enforce", "check_failed", False)):
            r.news, r.news_mode = FakeNews(verdict), mode
            eng = MeanReversionEngine("FOOUSDT", r.cfg)
            t = mk_trade()
            eng.trade = t
            r.handle({"type": "signal", "trade": t}, eng, NOW)
            if r.news_thread:
                r.news_thread.join(5)
                r.news_thread = None
            assert (eng.trade is not None) == kept, (mode, verdict)
            assert t.details["news"]["verdict"] == verdict


def test_news_report_reads_the_journal():
    from momentum_v2.reporting import JournalBridge
    from momentum_v2.core import Trade
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "j.db")
        jb = JournalBridge(db, scanner="mean_reversion_v2")
        for i, (verdict, r) in enumerate((("no_news", 1.0), ("big_news", -1.0), ("no_news", 0.5))):
            t = Trade(symbol=f"C{i}USDT", side="short", level_tf="fast_spike", level=1.0, signal_t=NOW,
                      entry_type="market", stop=1.05, target=0.95, target_kind="spike_origin", score=3,
                      criteria={"fast_spike": True}, setup="fast_spike",
                      details={"news": {"verdict": verdict, "hype": {"bot_share": 0.6, "posts_per_hour": 10}}})
            t.entry, t.entry_t, t.exit, t.exit_t, t.exit_reason = 1.0, NOW, 1.0 - 0.05 * r, NOW + 30 * MIN, "TP"
            t.gross_r = t.net_r = r
            jb.on_fill(t)
            jb.on_exit(t)
        flags = sqlite3.connect(db).execute("select flags from trades").fetchone()[0]
        assert "news:no_news" in flags and "setup:fast_spike" in flags
        out = subprocess.run([sys.executable, "-m", "momentum_v2.news_report", "--db", db],
                             cwd=ROOT, capture_output=True, text=True).stdout
        assert "news verdict" in out and "no_news" in out and "n=2" in out


def test_journal_ids_dont_collide_between_bots_or_backdated_signals():
    from journal.journal import Journal
    with tempfile.TemporaryDirectory() as d:
        j = Journal(str(Path(d) / "j.db"))
        ids = set()
        for scanner in ("momentum_v2", "mean_reversion_v2", "momentum_v2", "mean_reversion_v2"):
            for coin in ("AUSDT", "BUSDT"):
                ids.add(j.log_signal(scanner=scanner, direction="long", coin=coin + scanner[:3], sl_price=1,
                                     entry_price=2, signal_ts="2020-01-01T00:00:00+00:00"))
        assert len(ids) == 4 and any(i.startswith("MR2-") for i in ids) and any(i.startswith("MOMO2-") for i in ids)


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
