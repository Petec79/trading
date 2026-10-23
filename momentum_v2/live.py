#!/usr/bin/env python3
"""
v2 live PAPER runner — momentum or mean reversion.

Runs continuously, wakes a few seconds after every minute, processes each
watched coin's newly CLOSED 1-minute candles through the strategy engine,
and records fills/exits in the journal (scanner = "momentum_v2" or
"mean_reversion_v2"). No orders are ever sent to an exchange.

    python3 -m momentum_v2.live                              # momentum, forever
    python3 -m momentum_v2.live --strategy mean_reversion    # mean reversion, forever
    python3 -m momentum_v2.live --once --no-journal          # one dry cycle (testing)

Env (optional): DISCORD_SIGNALS_WEBHOOK, DISCORD_JOURNAL_WEBHOOK
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import binance
from .config import Config, TF_MS
from .core import LevelContext, SymbolEngine, period_start
from .mean_reversion import MRConfig, MeanReversionEngine, BTCSeries, funding_blocks
from .reporting import JournalBridge, discord, fmt_signal, fmt_exit, iso
from .alerts import Notifier

STATE_DIR = Path(__file__).resolve().parent / "data"
STATE_FILE = STATE_DIR / "live_state.json"

STRATEGIES = {
    "momentum": {"cfg": Config, "engine": SymbolEngine, "scanner": "momentum_v2",
                 "state": "live_state.json"},
    "mean_reversion": {"cfg": MRConfig, "engine": MeanReversionEngine, "scanner": "mean_reversion_v2",
                       "state": "mr_live_state.json"},
}


def recent_trade_counts(symbols: list[str], minutes: int, now_ms: int) -> dict[str, int]:
    """Trades in the last `minutes` closed 1m candles, per symbol (parallel)."""
    def one(sym):
        try:
            return sym, binance.trades_last_minutes(sym, minutes, now_ms)
        except Exception:
            return sym, None
    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sym, n in pool.map(one, symbols):
            if n is not None:
                out[sym] = n
    return out


class Runner:
    def __init__(self, cfg: Config | None = None, journal: bool = True, strategy: str = "momentum",
                 alerts: bool = False):
        spec = STRATEGIES[strategy]
        self.strategy = strategy
        self.is_mr = strategy == "mean_reversion"
        self.cfg = cfg or spec["cfg"]()
        self.engine_cls = spec["engine"]
        self.state_file = STATE_FILE if strategy == "momentum" else STATE_DIR / spec["state"]
        self.journal = JournalBridge(scanner=spec["scanner"]) if journal else None
        self.btc = None
        self.notifier = Notifier(strategy, self.cfg) if alerts else None
        # get-ready alerts: ALERT_WATCH=off to receive only actionable signals
        self.watch_alerts = os.environ.get("ALERT_WATCH", "on").lower() not in ("off", "0", "false")
        self.news_thread = None
        self._news_q: "queue.Queue" = queue.Queue()     # news results from the background thread
        self.news = None
        self.news_mode = os.environ.get("NEWS_MODE", getattr(self.cfg, "news_mode", "off"))
        if self.is_mr and self.news_mode != "off":
            from .news import NewsChecker
            self.news = NewsChecker(getattr(self.cfg, "news_lookback_min", 120))
        self.engines: dict[str, SymbolEngine] = {}
        self.allowed: dict[str, set] = {}
        self.last_bar_t: dict[str, int] = {}
        self.htf_cache: dict[str, dict] = {}
        self.universe_t = 0
        self.alt_perps: set[str] = set()
        self.load()

    # ── persistence ────────────────────────────────────────────────────────
    def load(self):
        if not self.state_file.exists():
            return
        st = json.loads(self.state_file.read_text())
        for sym, es in st.get("engines", {}).items():
            e = self.engine_cls(sym, self.cfg)
            e.load(es)
            self.engines[sym] = e
        self.last_bar_t = st.get("last_bar_t", {})
        self.allowed = {k: set(v) for k, v in st.get("allowed", {}).items()}

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        st = {"engines": {s: e.state() for s, e in self.engines.items()},
              "last_bar_t": self.last_bar_t,
              "allowed": {k: sorted(v) for k, v in self.allowed.items()}}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(self.state_file)

    # ── universe (p31-33) ──────────────────────────────────────────────────
    def refresh_universe(self, now_ms: int):
        cfg = self.cfg
        if now_ms - self.universe_t < cfg.universe_refresh_min * 60_000 and self.allowed:
            return
        if not self.alt_perps or now_ms - self.universe_t > 6 * 3_600_000:
            self.alt_perps = binance.tradable_alt_perps(set(cfg.majors))
        tick = [t for t in binance.tickers_24h() if t["symbol"] in self.alt_perps]
        # rough liquidity floor before the precise per-minute check at signal time
        tick = [t for t in tick if float(t.get("quoteVolume", 0)) >= cfg.min_quote_vol_per_min * 60 * 4]
        by_chg = sorted(tick, key=lambda t: float(t["priceChangePercent"]), reverse=True)
        gainers = [t["symbol"] for t in by_chg[:cfg.universe_gainers] if float(t["priceChangePercent"]) > 0]
        losers = [t["symbol"] for t in by_chg[::-1][:cfg.universe_losers] if float(t["priceChangePercent"]) < 0]
        hot = self.hot_by_recent_trades(tick, now_ms)
        allowed: dict[str, set] = {}
        if self.is_mr:
            # MR guide P1 p81-85: top gainers/losers + top trade count, both directions,
            # unless the return buckets are heavily one-sided (then trade with them).
            up = sum(1 for t in tick if float(t["priceChangePercent"]) > 0) / max(1, len(tick))
            sides = {"long"} if up >= 0.8 else {"short"} if up <= 0.2 else {"long", "short"}
            for s in gainers + losers + hot:
                allowed[s] = set(sides)
            print(f"[universe] return buckets: {up:.0%} of coins up → {sorted(sides)}", flush=True)
        else:
            for s in gainers:
                allowed.setdefault(s, set()).add("long")
            for s in losers:
                allowed.setdefault(s, set()).add("short")
            for s in hot:
                allowed.setdefault(s, set()).update({"long", "short"})
        # keep anything with a live order/position so it keeps being managed
        for s, e in self.engines.items():
            if e.trade:
                allowed.setdefault(s, set())
        self.allowed = allowed
        self.universe_t = now_ms
        print(f"[universe] {len(gainers)} gainers, {len(losers)} losers, {len(hot)} hot → "
              f"{len(allowed)} coins", flush=True)

    def hot_by_recent_trades(self, tick: list[dict], now_ms: int) -> list[str]:
        """Top coins by trade count over the last 5 minutes (Orion "TRD 5M").
        Falls back to the 24h count if Binance can't be polled."""
        cfg = self.cfg
        cands = [t["symbol"] for t in sorted(tick, key=lambda t: int(t.get("count", 0)), reverse=True)
                 [:cfg.trade_count_candidates]]
        counts = recent_trade_counts(cands, cfg.trade_count_minutes, now_ms)
        if len(counts) < len(cands) // 2:
            print("[universe] 5m trade counts unavailable, using 24h counts", flush=True)
            return cands[:cfg.universe_trade_count]
        top = sorted(counts, key=counts.get, reverse=True)[:cfg.universe_trade_count]
        print("[universe] hot (trades last 5m): " +
              ", ".join(f"{s.replace('USDT', '')} {counts[s]:,}" for s in top), flush=True)
        return top

    # ── levels ─────────────────────────────────────────────────────────────
    def context(self, sym: str, now_ms: int) -> LevelContext:
        cache = self.htf_cache.setdefault(sym, {})
        need = {"1h": 200, "4h": 12, "1d": 10, "1w": 5, "1M": 3}
        for tf, lim in need.items():
            period = period_start(now_ms, tf)          # Monday-aligned for 1w
            if cache.get(tf + "_p") != period:
                cache[tf] = binance.closed_klines(sym, tf, lim, now_ms)
                cache[tf + "_p"] = period
        ctx = LevelContext({tf: cache[tf] for tf in need}, self.cfg)
        ctx.btc = self.btc
        return ctx

    # ── one cycle ──────────────────────────────────────────────────────────
    def _fetch(self, sym: str, now_ms: int):
        """1m candles + levels for one coin. Retries briefly if Binance hasn't
        published the candle that just closed yet, so signals aren't a minute late."""
        want = (now_ms // 60_000) * 60_000 - 60_000          # open time of the candle that just closed
        bars = []
        for attempt in range(3):
            bars = binance.closed_klines(sym, "1m", self.cfg.history_bars, now_ms)
            if bars and bars[-1].t >= want:
                break
            time.sleep(0.4)
        return bars, self.context(sym, now_ms)

    def cycle(self):
        try:
            now_ms = binance.server_time_ms()          # Binance's clock, not ours
        except Exception:
            # local clock; still safe — closed_klines only keeps candles that have closed by now_ms
            now_ms = int(time.time() * 1000)
        self.apply_news_results()
        self.refresh_universe(now_ms)
        if self.is_mr:
            try:   # BTC candles for the "spike correlated with BTC" check (P1 p125)
                self.btc = BTCSeries(binance.closed_klines("BTCUSDT", "1m", 120, now_ms))
            except Exception as e:
                print(f"[btc] fetch failed: {e}", flush=True)
                self.btc = None
        # fetch every coin in parallel — the slow part — then run the strategy logic
        syms = list(self.allowed.items())
        fetched: dict[str, tuple] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {sym: pool.submit(self._fetch, sym, now_ms) for sym, _ in syms}
            for sym, f in futs.items():
                try:
                    fetched[sym] = f.result()
                except Exception as e:
                    print(f"[{sym}] fetch error: {e}", flush=True)
        open_count = sum(1 for e in self.engines.values() if e.trade)  # pending + open count toward the cap
        for sym, sides in syms:
            if sym not in fetched:
                continue
            try:
                bars, ctx = fetched[sym]
                ctx.btc = self.btc
                if len(bars) < 10:
                    continue
                eng = self.engines.setdefault(sym, self.engine_cls(sym, self.cfg))
                last = self.last_bar_t.get(sym)
                # Only replay missed candles when there is an order/position to
                # manage. A coin with nothing on starts from the newest candle,
                # so the bot never opens trades on stale candles.
                if last is None or (eng.trade is None and bars[-1].t - last > 5 * 60_000):
                    new_idx = [len(bars) - 1]
                else:
                    new_idx = [i for i, b in enumerate(bars) if b.t > last]
                if not new_idx:
                    continue
                for i in new_idx:
                    can_open = bool(sides) and open_count < self.cfg.max_open_positions
                    bar_close = bars[i].t + 60_000
                    for ev in eng.on_bar(bars[:i + 1], ctx, allowed=sides, can_open=can_open):
                        open_count += self.handle(ev, eng, now_ms, bar_close)
                    # checkpoint after every candle so a crash never replays it
                    self.last_bar_t[sym] = bars[i].t
                    self.save()
                eng.closed.clear()
                # get-ready alerts on the newest candle only
                if (self.notifier and self.watch_alerts and sides
                        and open_count < self.cfg.max_open_positions):
                    for w in eng.watch(bars, ctx, sides):
                        print(f"[watch] {w['symbol']} {w['side']} {w['setup']}", flush=True)
                        self.notifier.watch(w, now_ms)
            except Exception as e:
                print(f"[{sym}] error: {e}", flush=True)
                traceback.print_exc()
        # drop idle engines (and their bookmarks) for coins that left the universe
        for sym in [s for s, e in self.engines.items() if not e.trade and s not in self.allowed]:
            self.engines.pop(sym)
            self.last_bar_t.pop(sym, None)
            self.htf_cache.pop(sym, None)
        self.apply_news_results()
        self.save()
        # only report alive if the bot actually saw market data this cycle
        if fetched or not syms:
            self.heartbeat(now_ms)
        else:
            print("[cycle] no market data fetched — heartbeat NOT written", flush=True)
        live = [f"{e.symbol}:{e.trade.side}:{e.trade.status}" for e in self.engines.values() if e.trade]
        took = int(time.time() * 1000) - now_ms
        print(f"[cycle] {iso(now_ms)} watching {len(self.allowed)} | active {live or '-'} | {took} ms", flush=True)

    def heartbeat(self, now_ms: int):
        try:
            hb = STATE_DIR / f"heartbeat_{self.strategy}.json"
            hb.parent.mkdir(parents=True, exist_ok=True)
            hb.write_text(json.dumps({"ts": now_ms, "watching": len(self.allowed),
                                      "active": [e.symbol for e in self.engines.values() if e.trade]}))
        except Exception as e:
            print(f"[heartbeat] {e}", flush=True)

    def _journal(self, fn, t):
        """Journal/Discord problems are logged, never allowed to break trade state."""
        if not self.journal:
            return
        try:
            fn(t)
        except Exception as e:
            print(f"[journal] {t.symbol} write failed: {e}", flush=True)

    def _news_line(self) -> str:
        return {"shadow": "News: checking… (result follows; shadow mode)",
                "enforce": "News: checked ✓ none found"}.get(self.news_mode, "")

    def handle(self, ev: dict, eng=None, now_ms: int | None = None, bar_close: int | None = None) -> int:
        t = ev["trade"]
        kind = ev["type"]
        now_ms = now_ms or int(time.time() * 1000)
        bar_close = bar_close or now_ms
        n = self.notifier
        if kind == "signal" and self.is_mr and eng is not None:
            # P1 p92: funding > 0.1R due within 90 min → don't take the trade
            try:
                rate, nxt = binance.premium_index(t.symbol)
                ref = t.details.get("ref_entry") or t.level
                stop_pct = abs(ref - t.stop) / ref * 100
                t.details["funding"] = {"rate_pct": round(rate * 100, 4), "next": nxt}
                if funding_blocks(rate * 100, nxt, now_ms, stop_pct, t.side):
                    print(f"[funding] {t.symbol} skipped: funding {rate * 100:+.3f}% due soon", flush=True)
                    eng.trade = None
                    return 0
            except Exception as e:
                print(f"[funding] {t.symbol} check failed ({e}) — taking the trade", flush=True)
        if kind == "signal" and self.news is not None and t.setup == "fast_spike" and eng is not None:
            # MR guide P1 p122-124: don't fade news-driven spikes
            from .news import blocks
            if self.news_mode == "enforce":
                if n:
                    n.send(f"⏳ {t.symbol.replace('USDT', '')} {t.side.upper()} fast spike reversal — "
                           f"possible setup, checking news (≤60s)…")
                res = self._run_news(t, now_ms)
                if n:
                    n.news_result(t, res, self.news_mode)
                if blocks(res, self.news_mode):
                    eng.trade = None
                    print(f"[news] {t.symbol} fast spike skipped: {res['verdict']}", flush=True)
                    return 0
            else:
                # shadow: the signal goes out first, the news verdict follows as a reply
                self._signal_out(t, now_ms, bar_close, self._news_line())
                self.news_thread = threading.Thread(target=self._news_async, args=(t, now_ms), daemon=True)
                self.news_thread.start()
                return 1
        if kind == "signal":
            self._signal_out(t, now_ms, bar_close, self._news_line() if t.setup == "fast_spike" and self.news else "")
            return 1
        elif kind == "fill":
            print(f"[fill] {t.symbol} {t.side} @ {t.entry}", flush=True)
            self._journal(self.journal.on_fill if self.journal else None, t)
            if n:
                n.followup("fill", t, now_ms, bar_close)
        elif kind == "cancel":
            print(f"[cancel] {t.symbol} {t.side}: {t.exit_reason}", flush=True)
            if n:
                n.followup("cancel", t, now_ms, bar_close)
            return -1
        elif kind == "stop_move":
            print(f"[stop] {t.symbol} → {t.cur_stop} ({ev.get('why')})", flush=True)
            if n:
                n.followup("stop_move", t, now_ms, bar_close, ev.get("why", ""))
        elif kind == "exit":
            print(f"[exit] {t.symbol} {t.exit_reason} {t.net_r:+.2f}R", flush=True)
            self._journal(self.journal.on_exit if self.journal else None, t)
            if n:
                n.followup("exit", t, now_ms, bar_close)
            discord("journal", fmt_exit(t))
            return -1
        return 0

    def _signal_out(self, t, now_ms, bar_close, news_line=""):
        print(f"[signal] {t.symbol} {t.side} {t.setup} {t.entry_type} lvl={t.level} sl={t.stop} "
              f"tp={t.target} score={t.score}", flush=True)
        if self.notifier:
            self.notifier.signal(t, now_ms, news_line)
        else:
            discord("signals", fmt_signal(t))

    def apply_news_results(self):
        """Attach background news verdicts to their trades — on the main thread only,
        so state saves never see Trade.details change mid-write."""
        while True:
            try:
                t, res = self._news_q.get_nowait()
            except queue.Empty:
                return
            t.details["news"] = res

    def _run_news(self, t, now_ms, attach: bool = True) -> dict:
        try:
            res = self.news.check(t.symbol, now_ms, t.details.get("spike_start_t"))
        except Exception as e:
            res = {"verdict": "check_failed", "items": [], "hype": None, "errors": [str(e)[:200]]}
        if attach:
            t.details["news"] = res
        print(f"[news] {t.symbol} {res['verdict']} ({self.news_mode}) hype={res.get('hype')} "
              f"items={[i['title'][:60] for i in res['items']]} errors={res.get('errors')}", flush=True)
        return res

    def _news_async(self, t, now_ms):
        res = self._run_news(t, now_ms, attach=False)
        self._news_q.put((t, res))
        if self.notifier:
            self.notifier.news_result(t, res, self.news_mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="momentum")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-journal", action="store_true")
    ap.add_argument("--no-alerts", action="store_true", help="don't send Telegram/Discord alerts")
    a = ap.parse_args()
    r = Runner(journal=not a.no_journal, strategy=a.strategy, alerts=not a.no_alerts)
    while True:
        try:
            r.cycle()
        except Exception as e:
            print(f"[cycle] failed: {e}", flush=True)
            traceback.print_exc()
        if a.once:
            break
        # wake 0.6s after the next minute boundary: the candle has just closed
        # (_fetch retries briefly if Binance hasn't published it yet)
        time.sleep(60 - (time.time() % 60) + 0.6)


if __name__ == "__main__":
    main()
