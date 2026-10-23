#!/usr/bin/env python3
"""
Did the news filter and the hype score earn their place?

Reads closed mean_reversion_v2 fast-spike trades from the journal and compares
net R by news verdict and by hype (bot share, post rate). Run after a few weeks
of shadow-mode paper trading:

    python3 -m momentum_v2.news_report
    python3 -m momentum_v2.news_report --db /path/to/journal.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "journal" / "journal.db"


def details_from_notes(notes: str) -> dict:
    if not notes or "details " not in notes:
        return {}
    try:
        return json.loads(notes.split("details ", 1)[1])
    except Exception:
        return {}


def bucket_bot(x):
    if x is None:
        return "unknown"
    return "low (<20%)" if x < 0.2 else "mid (20-50%)" if x < 0.5 else "high (≥50%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    rows = con.execute("SELECT coin, r_multiple, notes FROM trades WHERE scanner='mean_reversion_v2' "
                       "AND outcome NOT IN ('pending','cancelled')").fetchall()
    trades = []
    for coin, r, notes in rows:
        d = details_from_notes(notes)
        news = d.get("news")
        if not news or r is None:
            continue
        hype = news.get("hype") or {}
        trades.append({"coin": coin, "r": r, "verdict": news.get("verdict"),
                       "bot": hype.get("bot_share"), "pph": hype.get("posts_per_hour")})
    print(f"Fast-spike trades with a news check: {len(trades)}")
    if not trades:
        print("Nothing yet — run the mean reversion bot in shadow mode for a while.")
        return
    pphs = sorted(t["pph"] for t in trades if t["pph"] is not None)
    cut = (pphs[len(pphs) // 3], pphs[2 * len(pphs) // 3]) if len(pphs) >= 6 else None

    def bucket_pph(x):
        if x is None or not cut:
            return "unknown"
        return "quiet" if x <= cut[0] else "normal" if x <= cut[1] else "loud"

    for title, key in (("news verdict", lambda t: t["verdict"]),
                       ("bot share of cashtag posts", lambda t: bucket_bot(t["bot"])),
                       ("cashtag post rate", lambda t: bucket_pph(t["pph"])),
                       ("no-news × bot share", lambda t: f"{t['verdict']} / {bucket_bot(t['bot'])}")):
        g = defaultdict(list)
        for t in trades:
            g[key(t)].append(t["r"])
        print(f"\nBy {title}:")
        for k in sorted(g):
            v = g[k]
            print(f"  {k:<32} n={len(v):<4} win={sum(r > 0 for r in v) / len(v) * 100:5.1f}%  "
                  f"avg={statistics.mean(v):+.3f}R  total={sum(v):+.2f}R")
    print("\nRule of thumb: don't act on a bucket with fewer than ~30 trades.")


if __name__ == "__main__":
    main()
