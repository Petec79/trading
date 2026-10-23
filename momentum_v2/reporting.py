"""Journal + Discord output for the v2 bots (momentum and mean reversion, paper only)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .core import Trade

SCANNER_NAME = "momentum_v2"          # journal scanner names: momentum_v2 | mean_reversion_v2
REPO_ROOT = Path(__file__).resolve().parent.parent


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _load_hermes_env():
    """Webhook URLs live in ~/.hermes/.env (never in git)."""
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


_load_hermes_env()


def discord(kind: str, content: str):
    """kind: 'signals' | 'journal'. Webhook URLs come from the environment only."""
    url = os.environ.get("DISCORD_SIGNALS_WEBHOOK" if kind == "signals" else "DISCORD_JOURNAL_WEBHOOK")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"content": content[:1900]}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "momentum-v2"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # never let Discord break the trading loop
        print(f"[discord] {kind} post failed: {e}", flush=True)


class JournalBridge:
    """Writes fills and exits into the shared PICO journal (journal/journal.db)."""

    def __init__(self, db_path: str | None = None, scanner: str = SCANNER_NAME):
        sys.path.insert(0, str(REPO_ROOT))
        from journal.journal import Journal  # noqa: E402
        self.j = Journal(db_path) if db_path else Journal()
        self.scanner = scanner

    def on_fill(self, t: Trade):
        crit = t.criteria
        t.journal_id = self.j.log_signal(
            scanner=self.scanner, direction=t.side, coin=t.symbol,
            entry_price=t.entry, sl_price=t.stop, tp_price=t.target,
            quality_score=t.score, flags=[k for k, v in crit.items() if v],
            level_approach=("fast_spike" if crit.get("fast_spike") or crit.get("slow_grind") is False
                            else "slow_grind" if crit.get("slow_grind") else "unclear"),
            volume_regime=("increasing" if crit.get("volume_increasing")
                           else "flat_or_decreasing" if crit.get("volume_not_increasing")
                           or crit.get("volume_increasing") is False else "increasing"),
            thesis=thesis(t), entry_type=t.entry_type, signal_ts=iso(t.signal_t))
        self.j.update_entry(t.journal_id, t.entry, t.entry_type, iso(t.entry_t))

    def on_exit(self, t: Trade):
        if not t.journal_id:          # fill write failed earlier → record it now
            self.on_fill(t)
        self.j.capture_exit(t.journal_id, t.exit, t.exit_reason, r_override=t.net_r,
                            notes=f"gross {t.gross_r:+.2f}R, net {t.net_r:+.2f}R after fees; "
                                  f"MFE {t.mfe_r:.2f}R; details {json.dumps(t.details, default=str)}")


SETUP_NAMES = {"momentum_breakout": "Momentum breakout", "failed_breakout": "MR failed breakout",
               "fast_spike": "MR fast spike reversal", "partial_fill": "MR partial fill (top/bottom tick)"}


def thesis(t: Trade) -> str:
    name = SETUP_NAMES.get(t.setup, t.setup)
    if t.setup == "momentum_breakout":
        where = f"close through prev {t.level_tf} {'high' if t.side == 'long' else 'low'} {t.level:g}"
    else:
        where = f"level {t.level:g}"
    return f"{name} {t.side.upper()} — {where} | score {t.score}/3 | target {t.target_kind}"


def fmt_signal(t: Trade) -> str:
    tick = lambda b: "✓" if b else "✗"  # noqa: E731
    crit = " ".join(f"{k.replace('_', ' ')} {tick(v)}" for k, v in t.criteria.items())
    tgt = "trail swings" if t.target is None else f"{t.target:g} ({t.target_kind})"
    return (f"🌶️ **{t.symbol}** {t.side.upper()} — {SETUP_NAMES.get(t.setup, t.setup)} "
            f"at `{t.level:g}` | {t.score}/3 ({crit})\n"
            f"{t.entry_type.upper()} | SL `{t.stop:g}` | TP {tgt} | paper")


def fmt_exit(t: Trade) -> str:
    icon = "✅" if t.net_r > 0.05 else "❌" if t.net_r < -0.05 else "➖"
    mins = (t.exit_t - t.entry_t) / 60_000
    return (f"{icon} **{t.symbol}** {t.side.upper()} ({SETUP_NAMES.get(t.setup, t.setup)}) closed "
            f"{t.exit_reason} — {t.net_r:+.2f}R net ({t.gross_r:+.2f}R gross) after {mins:.0f} min | paper")
