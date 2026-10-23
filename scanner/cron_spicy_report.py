#!/usr/bin/env python3
"""
Spicy Cron — Unified Scanner + Telegram Report
===============================================
Run both scanners, log signals to journal, produce a clean Telegram report.

Usage (run via cron agent):
    python cron_spicy_report.py

Output: prints the Telegram-ready report to stdout.
The cron agent will deliver this to Telegram.
"""

import subprocess, json, sys
from pathlib import Path
from datetime import datetime, timezone

SCANNER_DIR = Path(__file__).parent
WORKSPACE   = SCANNER_DIR.parent

def run_scanner(script: str, extra_args: list[str] = None) -> dict:
    """Run a scanner script and return (success, stdout, stderr)."""
    cmd = [sys.executable, str(SCANNER_DIR / script)]
    if extra_args:
        cmd += extra_args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=WORKSPACE
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr[:500] if result.stderr else "",
        }
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def build_report() -> str:
    """Build the combined Telegram report from latest JSON outputs."""
    from report_builder import build_mr_report, build_momo_report, _load_json

    parts = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # MR report
    mr_data = _load_json(SCANNER_DIR / "latest_spicy_scan.json")
    if mr_data:
        try:
            parts.append(build_mr_report(mr_data))
        except Exception as e:
            parts.append(f"MR report error: {e}")
    else:
        parts.append("MR scan: no data (scanner may have failed)")

    # Momentum report
    momo_data = _load_json(SCANNER_DIR / "latest_momentum_scan.json")
    if momo_data:
        try:
            parts.append("\n" + build_momo_report(momo_data))
        except Exception as e:
            parts.append(f"Momentum report error: {e}")
    else:
        parts.append("Momentum scan: no data (scanner may have failed)")

    return "\n".join(parts)


def main():
    print("Running Spicy scanners...", flush=True)

    # ── Run MR scanner ──────────────────────────────────────────────────────
    print("  MR scanner...", flush=True)
    mr = run_scanner("velo_spicy_scanner.py", ["--with-velo"])  # report only; trades → mean_reversion_v2
    if mr["success"]:
        print("    MR: OK", flush=True)
    else:
        print(f"    MR: FAILED — {mr['stderr'][:200]}", flush=True)

    # Small delay between scanners
    import time; time.sleep(2)

    # ── Run Momentum scanner ────────────────────────────────────────────────
    print("  Momentum scanner...", flush=True)
    momo = run_scanner("momentum_scanner.py", ["--with-velo"])  # report only; trades → momentum_v2
    if momo["success"]:
        print("    MOMO: OK", flush=True)
    else:
        print(f"    MOMO: FAILED — {momo['stderr'][:200]}", flush=True)

    # ── Build report ─────────────────────────────────────────────────────────
    print("\nBuilding report...\n", flush=True)
    report = build_report()
    print(report)


if __name__ == "__main__":
    main()
