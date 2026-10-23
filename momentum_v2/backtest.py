#!/usr/bin/env python3
"""
Momentum v2 — backtester. Replays 1-minute candles through the SAME engine
the live paper bot uses, with fees, slippage and conservative fills.

    python3 -m momentum_v2.backtest --symbols WIFUSDT,PEPEUSDT,FARTCOINUSDT \
        --start 2026-09-01 --end 2026-10-20
    python3 -m momentum_v2.backtest --symbols ... --side long --out trades.csv
    python3 -m momentum_v2.backtest --csv-dir /path/to/binance_vision_1m_csvs ...

Candles are cached in momentum_v2/data/ so re-runs are fast.

Fill rules (deliberately pessimistic):
  * signal only on a CLOSED candle; market entries fill at the NEXT open + slippage
  * limit entries fill only if a later candle trades back to the level
  * if stop and target are inside the same candle, the stop wins
  * no target can be hit on the candle a limit order fills
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .core import Bar, LevelContext, SymbolEngine, resample

DATA = Path(__file__).resolve().parent / "data"
WARMUP_DAYS = 8


def ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def load_bars(symbol: str, start: int, end: int, csv_dir: str | None) -> list[Bar]:
    if csv_dir:
        bars = []
        for f in sorted(Path(csv_dir).glob(f"{symbol}-1m-*.csv")):
            with open(f) as fh:
                for row in csv.reader(fh):
                    if not row or not row[0].isdigit():
                        continue
                    b = Bar.from_kline(row)
                    if start <= b.t < end:
                        bars.append(b)
        return sorted(bars, key=lambda b: b.t)
    DATA.mkdir(parents=True, exist_ok=True)
    cache = DATA / f"{symbol}_{start}_{end}.json"
    if cache.exists():
        return [Bar(*x) for x in json.loads(cache.read_text())]
    from . import binance
    print(f"  downloading {symbol} 1m ...", flush=True)
    bars = binance.history_1m(symbol, start, end)
    cache.write_text(json.dumps([[b.t, b.o, b.h, b.l, b.c, b.v, b.qv] for b in bars]))
    return bars


def run_symbol(symbol: str, bars: list[Bar], start: int, cfg: Config, sides: set) -> list:
    htf = {tf: resample(bars, tf) for tf in ("1h", "4h", "1d", "1w")}
    # drop the very first (partial) candle of each timeframe — warm-up starts mid-period
    htf = {tf: c[1:] for tf, c in htf.items()}
    ctx = LevelContext(htf, cfg)
    eng = SymbolEngine(symbol, cfg)
    cancelled = []
    keep = cfg.history_bars
    for i, b in enumerate(bars):
        if b.t < start:
            continue
        window = bars[max(0, i - keep + 1):i + 1]
        for ev in eng.on_bar(window, ctx, allowed=sides):
            if ev["type"] == "cancel":
                cancelled.append(ev["trade"])
    return eng.closed, cancelled


def summarise(trades: list, cancelled: list) -> str:
    out = []
    n = len(trades)
    out.append(f"Closed trades: {n}   (limit orders not filled/cancelled: {len(cancelled)})")
    if not n:
        return "\n".join(out)
    rs = [t.net_r for t in trades]
    wins = [r for r in rs if r > 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in sorted(trades, key=lambda t: t.exit_t):
        eq += r.net_r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gross_pos = sum(r for r in rs if r > 0)
    gross_neg = -sum(r for r in rs if r < 0)
    out.append(f"Win rate: {len(wins) / n * 100:.1f}%   Avg: {statistics.mean(rs):+.3f}R   "
               f"Total: {sum(rs):+.2f}R   Profit factor: "
               f"{(gross_pos / gross_neg) if gross_neg else float('inf'):.2f}   Max DD: {dd:.2f}R")
    out.append(f"Gross (before fees) total: {sum(t.gross_r for t in trades):+.2f}R")

    def table(title, key):
        g = defaultdict(list)
        for t in trades:
            g[key(t)].append(t.net_r)
        out.append(f"\nBy {title}:")
        for k in sorted(g, key=str):
            v = g[k]
            out.append(f"  {str(k):<22} n={len(v):<4} win={sum(1 for r in v if r > 0) / len(v) * 100:5.1f}%  "
                       f"avg={statistics.mean(v):+.3f}R  total={sum(v):+.2f}R")

    table("score", lambda t: f"{t.score}/3")
    table("exit reason", lambda t: t.exit_reason)
    table("entry type", lambda t: t.entry_type)
    table("level", lambda t: t.level_tf)
    table("side", lambda t: t.side)
    table("target", lambda t: t.target_kind)
    table("symbol", lambda t: t.symbol)
    c = defaultdict(int)
    for t in cancelled:
        c[t.exit_reason] += 1
    if c:
        out.append("\nCancelled orders: " + ", ".join(f"{k}={v}" for k, v in c.items()))
    return "\n".join(out)


def write_csv(trades: list, path: str):
    cols = ["symbol", "side", "level_tf", "level", "signal_t", "entry_type", "entry", "entry_t",
            "stop", "target", "target_kind", "score", "exit", "exit_t", "exit_reason",
            "gross_r", "net_r", "mfe_r", "criteria", "details"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for t in sorted(trades, key=lambda t: t.entry_t):
            d = t.to_dict()
            for k in ("signal_t", "entry_t", "exit_t"):
                d[k] = datetime.fromtimestamp(d[k] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            w.writerow([json.dumps(d[c]) if isinstance(d[c], dict) else d[c] for c in cols])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. WIFUSDT,PEPEUSDT")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--side", choices=["both", "long", "short"], default="both")
    ap.add_argument("--csv-dir", help="folder of Binance Vision 1m kline CSVs (offline)")
    ap.add_argument("--out", help="write trades to this CSV")
    ap.add_argument("--set", action="append", default=[],
                    help="override a config value, e.g. --set early_cut_enabled=False")
    a = ap.parse_args()
    cfg = Config()
    for kv in a.set:
        k, v = kv.split("=", 1)
        cur = getattr(cfg, k)
        setattr(cfg, k, (v.lower() == "true") if isinstance(cur, bool) else type(cur)(v))
    sides = {"long", "short"} if a.side == "both" else {a.side}
    start, end = ms(a.start), ms(a.end)
    all_trades, all_cancel = [], []
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        bars = load_bars(sym, start - WARMUP_DAYS * 86_400_000, end, a.csv_dir)
        if not bars:
            print(f"{sym}: no data")
            continue
        tr, ca = run_symbol(sym, bars, start, cfg, sides)
        print(f"{sym}: {len(bars)} bars → {len(tr)} trades, total {sum(t.net_r for t in tr):+.2f}R", flush=True)
        all_trades += tr
        all_cancel += ca
    print("\n" + summarise(all_trades, all_cancel))
    if a.out:
        write_csv(all_trades, a.out)
        print(f"\nTrades written to {a.out}")


if __name__ == "__main__":
    main()
