#!/usr/bin/env python3
"""
Spicy screener v2 — which coins are in which regime, right now.

Replaces scanner/market_scanner.py + scan_and_report.py and the daily
cron_spicy_report.py output. Built on the SAME measurements as the two v2
bots, so the screener and the bots can't disagree.

Both guides say the regime matters more than the entry ("check the weather
before flying the kite"). For each liquid alt perp it reports:
  * market bias from 24h return buckets (MR P1 p81-82): mostly green → mainly
    LONGS, mostly red → mainly SHORTS, otherwise both
  * MOMENTUM-friendly: 2h staircase + rising volume / grind (Momentum p60)
  * REVERSAL-friendly: sideways range + volume not rising, with how long the
    range has lasted (MR P1 p12, P2 p13)
  * AVOID reversals: staircases (MR P2 bad trades)
  * real 5-minute stats (Orion TRD/VOL/CHG 5M), nearest 1h/4h/1d/1w/1M levels,
    funding due soon, spikes in the last 10 minutes, and the bots' open trades

    python3 -m momentum_v2.screener            # print
    python3 -m momentum_v2.screener --send     # print + Telegram + Discord
Run every 15 min, e.g. cron:
    */15 * * * * cd /home/openclaw/workspace && /usr/bin/python3 -m momentum_v2.screener --send
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import binance
from .config import Config
from .core import LevelContext, slow_grind, staircase, volume_increasing
from .mean_reversion import MRConfig, find_spike, is_ranging, range_duration_hours, volume_not_increasing

DATA = Path(__file__).resolve().parent / "data"
HTF_NEED = {"1h": 200, "4h": 12, "1d": 10, "1w": 5, "1M": 3}


def bias_from_returns(changes: list[float]) -> dict:
    up = sum(1 for c in changes if c > 0) / max(1, len(changes))
    if up >= 0.8:
        return {"up_share": up, "bias": "BULLISH", "advice": "mainly LONGS (buy support / momentum longs)"}
    if up <= 0.2:
        return {"up_share": up, "bias": "BEARISH", "advice": "mainly SHORTS (short resistance / momentum shorts)"}
    return {"up_share": up, "bias": "MIXED", "advice": "both directions"}


def analyse(sym: str, bars: list, ctx: LevelContext, cfg: Config, mrc: MRConfig) -> dict:
    """All regime measurements for one coin, from closed 1m candles."""
    c = bars[-1].c
    now = bars[-1].t + 60_000
    out = {"symbol": sym, "price": c,
           "chg_5m_pct": round((c - bars[-6].c) / bars[-6].c * 100, 2),
           "vol_5m_usd": round(sum(b.qv for b in bars[-5:])),
           "vol_1m_avg_usd": round(sum(b.qv for b in bars[-60:]) / 60)}
    # momentum (Momentum guide p60): staircase required + grind / rising volume
    vol_up, dv = volume_increasing(bars, cfg)
    out["volume_ratio_1h"] = dv.get("vol_ratio")
    for d, side in ((1, "long"), (-1, "short")):
        st, _ = staircase(bars, d, cfg)
        gr, _ = slow_grind(bars, d, cfg)
        out[f"mom_{side}"] = {"staircase": st, "grind": gr, "volume_up": vol_up,
                              "score": int(st) + int(gr) + int(vol_up)}
    # mean reversion (MR P1 p12): range + volume not increasing; staircase = avoid
    vni, dvn = volume_not_increasing(bars, mrc)
    out["mr"] = {"volume_not_increasing": vni, **dvn,
                 "range_hours": range_duration_hours(bars, mrc)}
    for d, side in ((1, "long"), (-1, "short")):
        rg, drg = is_ranging(bars[-120:], d, mrc)
        out["mr"][f"ranging_for_{side}s"] = rg
        out["mr"]["ma_crosses_2h"] = drg.get("ma_crosses_2h")
    # spike in the last 10 minutes (either way)
    spikes = []
    for ds in (1, -1):
        for k in range(len(bars) - 10, len(bars)):
            sp = find_spike(bars, k, ds, mrc)
            if sp:
                spikes.append({"dir": "up" if ds == 1 else "down", "size_pct": sp["size_pct"],
                               "vol_x": sp["vol_x"], "mins_ago": len(bars) - 1 - k})
                break
    out["spikes_10m"] = spikes
    # nearest levels
    above, below = [], []
    for tf in cfg.level_timeframes:
        b = ctx.prev(tf, now)
        if not b:
            continue
        for p, name in ((b.h, f"{tf} high"), (b.l, f"{tf} low")):
            (above if p > c else below).append((p, name))
    if above:
        p, n = min(above)
        out["level_above"] = {"price": p, "name": n, "dist_pct": round((p - c) / c * 100, 2)}
    if below:
        p, n = max(below)
        out["level_below"] = {"price": p, "name": n, "dist_pct": round((p - c) / c * 100, 2)}
    # classification
    ml, ms = out["mom_long"], out["mom_short"]
    tags = []
    if ml["staircase"] and ml["score"] >= cfg.min_score:
        tags.append("momentum_long")
    if ms["staircase"] and ms["score"] >= cfg.min_score:
        tags.append("momentum_short")
    stair_any = ml["staircase"] or ms["staircase"]
    ranging = out["mr"]["ranging_for_longs"] or out["mr"]["ranging_for_shorts"]
    if ranging and not stair_any and (vni or out["mr"]["range_hours"] >= mrc.dor_exception_hours):
        tags.append("reversal")
    if stair_any:
        tags.append("avoid_reversal_" + ("shorts" if ml["staircase"] else "longs"))
    out["tags"] = tags
    return out


def open_bot_trades() -> list[dict]:
    rows = []
    for name, f in (("momentum", "live_state.json"), ("mean_reversion", "mr_live_state.json")):
        try:
            st = json.loads((DATA / f).read_text())
        except Exception:
            continue
        for sym, e in st.get("engines", {}).items():
            t = e.get("trade")
            if t:
                rows.append({"bot": name, "symbol": sym, "side": t["side"], "setup": t.get("setup"),
                             "status": t["status"], "entry": t.get("entry"), "level": t.get("level"),
                             "stop": t.get("cur_stop") or t.get("stop"), "target": t.get("target")})
    return rows


def build(now_ms: int | None = None) -> dict:
    from .live import recent_trade_counts
    cfg, mrc = Config(), MRConfig()
    now_ms = now_ms or binance.server_time_ms()
    alts = binance.tradable_alt_perps(set(cfg.majors))
    tick = [t for t in binance.tickers_24h() if t["symbol"] in alts
            and float(t.get("quoteVolume", 0)) >= cfg.min_quote_vol_per_min * 60 * 4]
    bias = bias_from_returns([float(t["priceChangePercent"]) for t in tick])
    by_chg = sorted(tick, key=lambda t: float(t["priceChangePercent"]), reverse=True)
    gainers = [t["symbol"] for t in by_chg[:cfg.universe_gainers]]
    losers = [t["symbol"] for t in by_chg[::-1][:cfg.universe_losers]]
    by_24h = [t["symbol"] for t in sorted(tick, key=lambda t: int(t.get("count", 0)), reverse=True)
              [:cfg.trade_count_candidates]]
    counts = recent_trade_counts(by_24h, cfg.trade_count_minutes, now_ms)
    hot = sorted(counts, key=counts.get, reverse=True)[:cfg.universe_trade_count] or by_24h[:cfg.universe_trade_count]
    chg24 = {t["symbol"]: float(t["priceChangePercent"]) for t in tick}
    cands = list(dict.fromkeys(gainers + losers + hot))
    try:
        funding = binance.premium_all()
    except Exception:
        funding = {}

    def one(sym):
        bars = binance.closed_klines(sym, "1m", 400, now_ms)
        htf = {tf: binance.closed_klines(sym, tf, n, now_ms) for tf, n in HTF_NEED.items()}
        return sym, bars, LevelContext(htf, cfg)

    coins = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for fut in [pool.submit(one, s) for s in cands]:
            try:
                sym, bars, ctx = fut.result()
            except Exception as e:
                print(f"[screener] fetch failed: {e}", flush=True)
                continue
            if len(bars) < 260:
                continue
            a = analyse(sym, bars, ctx, cfg, mrc)
            a["chg_24h_pct"] = chg24.get(sym)
            a["trades_5m"] = counts.get(sym)
            a["source"] = [n for n, lst in (("gainer", gainers), ("loser", losers), ("hot", hot)) if sym in lst]
            if a["vol_1m_avg_usd"] < cfg.min_quote_vol_per_min:
                a["tags"].append("illiquid")
            rate, nxt = funding.get(sym, (0.0, None))
            a["funding_pct"] = round(rate * 100, 4)
            a["funding_in_min"] = None if not nxt else round((nxt - now_ms) / 60_000)
            coins.append(a)
    return {"ts": now_ms, "bias": bias, "coins": coins, "open_trades": open_bot_trades()}


def _sym(s):
    return s.replace("USDT", "")


def format_report(rep: dict, top: int = 6) -> str:
    t = datetime.fromtimestamp(rep["ts"] / 1000, tz=timezone.utc).strftime("%H:%M UTC %d %b")
    b = rep["bias"]
    coins = [c for c in rep["coins"] if "illiquid" not in c["tags"]]
    L = [f"🌶️ SPICY SCREENER — {t}",
         f"Bias: {b['up_share'] * 100:.0f}% of liquid alts up on 24h → {b['bias']}: {b['advice']}"]

    def stats(c):
        s = f"24h {c['chg_24h_pct']:+.1f}% · 5m {c['chg_5m_pct']:+.2f}% · ${c['vol_5m_usd'] / 1e6:.1f}M/5m"
        if c.get("trades_5m"):
            s += f" · {c['trades_5m'] / 1000:.1f}k trades/5m"
        return s

    def lvl(c, key):
        x = c.get(key)
        return f"{x['name']} {x['price']:g} ({x['dist_pct']:+.2f}%)" if x else "—"

    for side, icon in (("long", "🟢"), ("short", "🔴")):
        rows = sorted([c for c in coins if f"momentum_{side}" in c["tags"]],
                      key=lambda c: -c[f"mom_{side}"]["score"])[:top]
        if rows:
            L.append(f"\n⚡ MOMENTUM {side.upper()} (2h staircase{' up' if side == 'long' else ' down'} + grind/volume)")
            for c in rows:
                m = c[f"mom_{side}"]
                nxt = lvl(c, "level_above" if side == "long" else "level_below")
                L.append(f"{icon} {_sym(c['symbol'])} {m['score']}/3 | {stats(c)}\n   next level: {nxt}")
    rev = sorted([c for c in coins if "reversal" in c["tags"]],
                 key=lambda c: (-c["mr"]["range_hours"], c["mr"].get("vol_vs_regime_start") or 9))[:top]
    if rev:
        L.append("\n🔄 REVERSAL-FRIENDLY (range + volume not rising)")
        for c in rev:
            mr = c["mr"]
            L.append(f"• {_sym(c['symbol'])} ranging {mr['range_hours']}h+ · vol {mr.get('vol_vs_regime_start')}x regime start"
                     f" | {stats(c)}\n   above: {lvl(c, 'level_above')} · below: {lvl(c, 'level_below')}")
    av = [c for c in coins if any(x.startswith("avoid_reversal") for x in c["tags"])]
    if av:
        L.append("\n🚫 AVOID REVERSALS (staircase): " + ", ".join(
            f"{_sym(c['symbol'])} {'↑' if c['mom_long']['staircase'] else '↓'}" for c in av[:12]))
    sp = [c for c in coins if c["spikes_10m"]]
    if sp:
        L.append("\n⚡ SPIKES (last 10 min): " + ", ".join(
            f"{_sym(c['symbol'])} {s['dir']} {s['size_pct']:.1f}% ({s['vol_x']:.0f}x vol, {s['mins_ago']}m ago)"
            for c in sp[:8] for s in c["spikes_10m"][:1]))
    fund = [c for c in coins if c.get("funding_in_min") is not None and 0 <= c["funding_in_min"] <= 90
            and abs(c["funding_pct"]) >= 0.05]
    if fund:
        L.append("\n💸 FUNDING due ≤90 min: " + ", ".join(
            f"{_sym(c['symbol'])} {c['funding_pct']:+.3f}% in {c['funding_in_min']}m "
            f"({'longs' if c['funding_pct'] > 0 else 'shorts'} pay)" for c in fund[:8]))
    ot = rep.get("open_trades") or []
    L.append("\n📌 BOTS NOW: " + ("; ".join(
        f"{o['bot']} {_sym(o['symbol'])} {o['side']} {o['status']} "
        f"{'@ ' + format(o['entry'], 'g') if o.get('entry') else 'limit ' + format(o['level'], 'g')} "
        f"SL {o['stop']:g}" + (f" TP {o['target']:g}" if o.get("target") else "")
        for o in ot) if ot else "no open trades"))
    L.append("paper — context for manual trading, not advice")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="also send to Telegram + Discord")
    a = ap.parse_args()
    t0 = time.time()
    rep = build()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "screener_latest.json").write_text(json.dumps(rep, indent=1, default=str))
    text = format_report(rep)
    print(text)
    print(f"\n({len(rep['coins'])} coins in {time.time() - t0:.1f}s)")
    if a.send:
        from .alerts import Notifier
        Notifier("screener").send(text)
    (DATA / "heartbeat_screener.json").write_text(json.dumps({"ts": rep["ts"]}))


if __name__ == "__main__":
    main()
