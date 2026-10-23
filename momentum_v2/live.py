#!/usr/bin/env python3
"""
Momentum v2 — live PAPER runner.

Runs continuously, wakes a few seconds after every minute, processes each
watched coin's newly CLOSED 1-minute candles through the strategy engine,
and records fills/exits in the journal (scanner = "momentum_v2").
No orders are ever sent to an exchange.

    python3 -m momentum_v2.live              # run forever (use the systemd unit)
    python3 -m momentum_v2.live --once       # one cycle, then exit (testing)
    python3 -m momentum_v2.live --no-journal # don't write to journal.db

Env (optional): DISCORD_SIGNALS_WEBHOOK, DISCORD_JOURNAL_WEBHOOK
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from . import binance
from .config import Config, TF_MS
from .core import LevelContext, SymbolEngine, period_start
from .reporting import JournalBridge, discord, fmt_signal, fmt_exit, iso

STATE_DIR = Path(__file__).resolve().parent / "data"
STATE_FILE = STATE_DIR / "live_state.json"


class Runner:
    def __init__(self, cfg: Config, journal: bool = True):
        self.cfg = cfg
        self.journal = JournalBridge() if journal else None
        self.engines: dict[str, SymbolEngine] = {}
        self.allowed: dict[str, set] = {}
        self.last_bar_t: dict[str, int] = {}
        self.htf_cache: dict[str, dict] = {}
        self.universe_t = 0
        self.alt_perps: set[str] = set()
        self.load()

    # ── persistence ────────────────────────────────────────────────────────
    def load(self):
        if not STATE_FILE.exists():
            return
        st = json.loads(STATE_FILE.read_text())
        for sym, es in st.get("engines", {}).items():
            e = SymbolEngine(sym, self.cfg)
            e.load(es)
            self.engines[sym] = e
        self.last_bar_t = st.get("last_bar_t", {})
        self.allowed = {k: set(v) for k, v in st.get("allowed", {}).items()}

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        st = {"engines": {s: e.state() for s, e in self.engines.items()},
              "last_bar_t": self.last_bar_t,
              "allowed": {k: sorted(v) for k, v in self.allowed.items()}}
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(STATE_FILE)

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
        hot = [t["symbol"] for t in sorted(tick, key=lambda t: int(t.get("count", 0)), reverse=True)
               [:cfg.universe_trade_count]]
        allowed: dict[str, set] = {}
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

    # ── levels ─────────────────────────────────────────────────────────────
    def context(self, sym: str, now_ms: int) -> LevelContext:
        cache = self.htf_cache.setdefault(sym, {})
        need = {"1h": 200, "4h": 12, "1d": 10, "1w": 5}
        for tf, lim in need.items():
            period = period_start(now_ms, tf)          # Monday-aligned for 1w
            if cache.get(tf + "_p") != period:
                cache[tf] = binance.closed_klines(sym, tf, lim, now_ms)
                cache[tf + "_p"] = period
        return LevelContext({tf: cache[tf] for tf in need}, self.cfg)

    # ── one cycle ──────────────────────────────────────────────────────────
    def cycle(self):
        try:
            now_ms = binance.server_time_ms()          # Binance's clock, not ours
        except Exception:
            now_ms = int(time.time() * 1000) - 2_000
        self.refresh_universe(now_ms)
        open_count = sum(1 for e in self.engines.values() if e.trade)  # pending + open count toward the cap
        for sym, sides in list(self.allowed.items()):
            try:
                bars = binance.closed_klines(sym, "1m", self.cfg.history_bars, now_ms)
                if len(bars) < 10:
                    continue
                eng = self.engines.setdefault(sym, SymbolEngine(sym, self.cfg))
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
                ctx = self.context(sym, now_ms)
                for i in new_idx:
                    can_open = bool(sides) and open_count < self.cfg.max_open_positions
                    for ev in eng.on_bar(bars[:i + 1], ctx, allowed=sides, can_open=can_open):
                        open_count += self.handle(ev)
                    # checkpoint after every candle so a crash never replays it
                    self.last_bar_t[sym] = bars[i].t
                    self.save()
                eng.closed.clear()
            except Exception as e:
                print(f"[{sym}] error: {e}", flush=True)
                traceback.print_exc()
        # drop idle engines (and their bookmarks) for coins that left the universe
        for sym in [s for s, e in self.engines.items() if not e.trade and s not in self.allowed]:
            self.engines.pop(sym)
            self.last_bar_t.pop(sym, None)
            self.htf_cache.pop(sym, None)
        self.save()
        live = [f"{e.symbol}:{e.trade.side}:{e.trade.status}" for e in self.engines.values() if e.trade]
        print(f"[cycle] {iso(now_ms)} watching {len(self.allowed)} | active {live or '-'}", flush=True)

    def _journal(self, fn, t):
        """Journal/Discord problems are logged, never allowed to break trade state."""
        if not self.journal:
            return
        try:
            fn(t)
        except Exception as e:
            print(f"[journal] {t.symbol} write failed: {e}", flush=True)

    def handle(self, ev: dict) -> int:
        t = ev["trade"]
        kind = ev["type"]
        if kind == "signal":
            print(f"[signal] {t.symbol} {t.side} {t.entry_type} lvl={t.level} sl={t.stop} "
                  f"tp={t.target} score={t.score} {t.details}", flush=True)
            discord("signals", fmt_signal(t))
            return 1
        elif kind == "fill":
            print(f"[fill] {t.symbol} {t.side} @ {t.entry}", flush=True)
            self._journal(self.journal.on_fill if self.journal else None, t)
        elif kind == "cancel":
            print(f"[cancel] {t.symbol} {t.side}: {t.exit_reason}", flush=True)
            return -1
        elif kind == "stop_move":
            print(f"[stop] {t.symbol} → {t.cur_stop} ({ev.get('why')})", flush=True)
        elif kind == "exit":
            print(f"[exit] {t.symbol} {t.exit_reason} {t.net_r:+.2f}R", flush=True)
            self._journal(self.journal.on_exit if self.journal else None, t)
            discord("journal", fmt_exit(t))
            return -1
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-journal", action="store_true")
    a = ap.parse_args()
    r = Runner(Config(), journal=not a.no_journal)
    while True:
        try:
            r.cycle()
        except Exception as e:
            print(f"[cycle] failed: {e}", flush=True)
            traceback.print_exc()
        if a.once:
            break
        # wake 3s after the next minute boundary (candle has closed by then)
        time.sleep(60 - (time.time() % 60) + 3)


if __name__ == "__main__":
    main()
