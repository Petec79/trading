#!/usr/bin/env python3
"""
Heartbeat watchdog — so silence never means "bot down".

Each bot writes momentum_v2/data/heartbeat_<strategy>.json every cycle (every
minute); the screener writes heartbeat_screener.json every run. Run this from
cron every 2 minutes:

    */2 * * * * cd /home/openclaw/workspace && /usr/bin/python3 -m momentum_v2.watchdog

It sends ONE Telegram/Discord alert when something goes quiet, and one when it
recovers (state kept in data/watchdog_state.json).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .alerts import Notifier

DATA = Path(__file__).resolve().parent / "data"
EXPECT = {                      # name: max silence in seconds before alerting
    "momentum": 180,
    "mean_reversion": 180,
    "screener": 45 * 60,
}


def check(now_s: float | None = None, notifier: Notifier | None = None, expect: dict | None = None) -> list[str]:
    now_s = now_s or time.time()
    notifier = notifier or Notifier("watchdog")
    expect = expect or EXPECT
    state_f = DATA / "watchdog_state.json"
    try:
        state = json.loads(state_f.read_text())
    except Exception:
        state = {}
    msgs = []
    for name, limit in expect.items():
        f = DATA / f"heartbeat_{name}.json"
        if not f.exists():
            continue                          # not deployed (yet) — nothing to watch
        try:
            age = now_s - json.loads(f.read_text())["ts"] / 1000
        except Exception:
            age = float("inf")
        down = age > limit
        was_down = state.get(name) == "down"
        if down and not was_down:
            m = f"{name} bot silent for {age / 60:.0f} min — NO signals are being generated. Check: systemctl --user status {name.replace('_', '-')}-v2"
            notifier.heartbeat_problem(m)
            msgs.append(m)
            state[name] = "down"
        elif not down and was_down:
            m = f"{name} is running again."
            notifier.send(f"✅ {m}")
            msgs.append(m)
            state[name] = "up"
        elif not down:
            state[name] = "up"
    DATA.mkdir(parents=True, exist_ok=True)
    state_f.write_text(json.dumps(state))
    return msgs


if __name__ == "__main__":
    for m in check():
        print(m)
