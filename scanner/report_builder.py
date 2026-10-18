#!/usr/bin/env python3
"""
Spicy Report Builder
====================
Reads the latest scanner JSON outputs and builds a clean Telegram-ready report.

Usage:
    python report_builder.py                    # full MR + Momentum combo
    python report_builder.py --momo-only        # momentum scanner only
    python report_builder.py --mr-only         # mean reversion only
"""

import json, sys, argparse
from datetime import datetime, timezone
from pathlib import Path

SCANNER_DIR = Path(__file__).parent
MR_JSON  = SCANNER_DIR / "latest_spicy_scan.json"
MOMO_JSON = SCANNER_DIR / "latest_momentum_scan.json"
JOURNAL_DIR = Path(__file__).parent.parent / "journal"


def _load_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _clean(sym: str) -> str:
    """Strip USDT suffix and map unicode-heavy names to ASCII."""
    s = sym.replace("USDT", "")
    # Map known unicode coin names to short ASCII
    DISPLAY_NAMES = {
        "bnb": "BNB", "busd": "BUSD", "tusd": "TUSD",
        "粒": "粒",
    }
    return DISPLAY_NAMES.get(s, s)


def _funding_note(c: dict) -> str | None:
    """Return a one-line funding warning if applicable."""
    if c.get("skip") and "funding" in c.get("skip", "").lower():
        fr = c.get("funding_rate") or 0
        nxt = c.get("next_fund", "?")
        return f"⚠️ funding {fr:+.2f}% at {nxt}"
    return None


# ── MR Report ─────────────────────────────────────────────────────────────────

def build_mr_report(data: dict, include_checks: bool = True) -> str:
    bias     = data["bias"]["value"]
    strength = data["bias"]["strength"]
    bias_raw = data.get("bias", {}).get("raw", "")
    spagh    = data["spaghetti"]
    sp_class = spagh["classification"]
    sp_yaxis = spagh.get("y_axis_max", 0)
    parabolic = spagh.get("parabolic_coins", [])
    longs    = data["candidates"]["longs"]
    shorts   = data["candidates"]["shorts"]
    stairs   = data["candidates"].get("stairs", [])
    fast_spikes = data["candidates"].get("fast_spikes", [])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_h = datetime.now(timezone.utc).hour

    lines = [
        f"SCANNER — {today}",
        f"═══════════════════════════════════════",
        "",
        f"Bias: {bias}/{strength}" +
        (f"  ({bias_raw})" if bias_raw else ""),
        f"Spaghetti: {sp_class} — y-axis {sp_yaxis}%",
    ]

    # Parabolic coins
    para_clean = [_clean(c) for c in parabolic[:5]]
    if para_clean:
        lines.append(f"Parabolic: {', '.join(para_clean)}")

    # Edge by session
    if 0 <= now_h < 8:
        edge = "Asian flush territory — high conviction"
    elif 8 <= now_h < 12:
        edge = "London AM — strong edge (83% WR)"
    elif 12 <= now_h < 16:
        edge = "London PM — mixed results, monitor"
    else:
        edge = "Late session — Asian flush territory"
    lines.append(f"Edge: {edge}")

    # Funding alerts
    funding_coins = [c for c in longs + shorts if c.get("skip") and "funding" in c["skip"].lower()]
    if funding_coins:
        lines.append("")
        lines.append("Funding alerts:")
        for c in funding_coins[:4]:
            sym = _clean(c["symbol"])
            fr = c.get("funding_rate") or 0
            nxt = c.get("next_fund", "?")
            dirn = "SHORT" if c["direction"] == "SHORT" else "LONG"
            lines.append(f"  {sym}: {fr:+.2f}% funding — skip {dirn}")

    # Shorts
    valid_shorts = [c for c in shorts if not c.get("skip")]
    if valid_shorts:
        lines.append("")
        lines.append("Top Reversal SHORT (coins UP, fade the top):")
        lines.append(f"{'Coin':<12}{'24h %':>9}{'MR Score':>10}")
        lines.append("-" * 35)
        for c in valid_shorts[:8]:
            sym = _clean(c["symbol"])
            chg = c["chg24"]
            score = c["score"]
            flag = ""
            if c.get("in_top10_trd"):
                flag = " ★"
            elif c.get("is_fast_spike"):
                flag = " ⚡"
            lines.append(f"{sym:<12}{chg:>+8.2f}%{score:>9.1f}{flag}")

    # Longs
    valid_longs = [c for c in longs if not c.get("skip")]
    if valid_longs:
        lines.append("")
        lines.append("Top Reversal LONG (coins DOWN, buy the dip):")
        lines.append(f"{'Coin':<12}{'24h %':>9}{'MR Score':>10}")
        lines.append("-" * 35)
        for c in valid_longs[:8]:
            sym = _clean(c["symbol"])
            chg = c["chg24"]
            score = c["score"]
            flag = ""
            if c.get("in_top10_trd"):
                flag = " ★"
            elif c.get("is_fast_spike"):
                flag = " ⚡"
            lines.append(f"{sym:<12}{chg:>+8.2f}%{score:>9.1f}{flag}")

    # Staircase
    if stairs:
        avoid = [_clean(c["symbol"]) for c in stairs[:6]]
        lines.append("")
        lines.append(f"Avoid (staircase): {', '.join(avoid)}")

    if include_checks:
        lines += [
            "",
            "Checks:",
            "  ✅ vol ≥ $500K/5m | trd ≥ 5K/5m | no majors",
            "  ⚡ fast spike → check news/BTC before entry",
            "  ⚠️ funding due <90min → skip that direction",
        ]

    lines.append("")
    lines.append("Bottom line: " + _bottom_line(bias, valid_shorts, valid_longs, now_h))

    return "\n".join(lines)


def _bottom_line(bias: str, shorts, longs, hour: int) -> str:
    if not shorts and not longs:
        return "No valid candidates today. Sit out."
    top_short = shorts[0] if shorts else None
    top_long  = longs[0] if longs else None
    parts = []
    if hour >= 12:
        parts.append("Late session — Asian flush tonight for best edge.")
    if top_short and top_short["score"] >= 65:
        sym = _clean(top_short["symbol"])
        parts.append(f"Short {sym} (MR {top_short['score']:.0f}) — watch for extension then fade.")
    if top_long and top_long["score"] >= 65:
        sym = _clean(top_long["symbol"])
        parts.append(f"Long {sym} (MR {top_long['score']:.0f}) — only if pulling back from open.")
    if bias == "MIXED":
        parts.append("Mixed bias — size small, strict SL.")
    return " ".join(parts) if parts else "Moderate setup — wait for cleaner entries."


# ── Momentum Report ───────────────────────────────────────────────────────────

def build_momo_report(data: dict) -> str:
    results = data.get("coins", [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Filter signals
    get_ready = [r for r in results if r.get("get_ready")]
    momentum  = [r for r in results if r.get("momentum")]
    avoid     = [r for r in results if r.get("is_staircase")]

    lines = [
        f"MOMENTUM SCAN — {today}",
        f"{'='*32}",
        "",
    ]

    if get_ready:
        lines.append("GET_READY (watch for trigger):")
        for r in get_ready[:6]:
            sym  = _clean(r.get("symbol", ""))
            d    = r.get("get_ready_direction", "short").upper()
            q    = r.get("quality_score", 0)
            qstr = "🟢" * q + "⚪" * (3 - q)
            dist = r.get("dist_to_sl_pct") or r.get("dist_to_sh_pct") or 0
            icon = "🔴" if d == "SHORT" else "🟢"
            lines.append(f"  {icon} {sym} {qstr} {d} — {dist:.1f}% to SL")
        lines.append("")

    if momentum:
        lines.append("MOMENTUM TRADES (active):")
        for r in momentum[:6]:
            sym  = _clean(r.get("symbol", ""))
            d    = r.get("direction", "short").upper()
            q    = r.get("quality_score", 0)
            qstr = "🟢" * q + "⚪" * (3 - q)
            icon = "🔴" if d == "SHORT" else "🟢"
            lines.append(f"  {icon} {sym} {qstr} {d} — TRIGGERED")
        lines.append("")

    if avoid:
        avoid_names = [_clean(r.get("symbol", "")) for r in avoid[:6]]
        lines.append(f"Avoid (staircase): {', '.join(avoid_names)}")
        lines.append("")

    if not get_ready and not momentum:
        lines.append("No momentum setups today.")
        lines.append("")

    lines.append("Staircase rule: HH/HL for longs, LH/LS for shorts. "
                 "Minimum 2h structure. Volume confirmation required.")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--momo-only", action="store_true")
    parser.add_argument("--mr-only", action="store_true")
    args = parser.parse_args()

    parts = []

    if not args.momo_only:
        data = _load_json(MR_JSON)
        if data:
            parts.append(build_mr_report(data))
        else:
            parts.append("MR scan: no data yet (run scanner first)")

    if not args.mr_only:
        data = _load_json(MOMO_JSON)
        if data:
            if parts:
                parts.append("")
            parts.append(build_momo_report(data))
        else:
            parts.append("Momentum scan: no data yet (run scanner first)")

    report = "\n".join(parts)
    print(report)

    # Optionally save
    out_path = SCANNER_DIR / "latest_telegram_report.txt"
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
