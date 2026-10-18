#!/usr/bin/env python3
"""
Spicy Real-Time Scan — runs every 30 min.
Posts signals to Discord #signals, trade results to #journal.
No Telegram output (that's the daily 16:00 cron job).
"""
import subprocess, sys
from pathlib import Path

SCANNER_DIR = Path(__file__).parent

def run(script, args=None):
    cmd = [sys.executable, str(SCANNER_DIR / script)]
    if args:
        cmd += args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(SCANNER_DIR.parent))
    return r.returncode == 0, r.stdout, r.stderr

# MR scanner + Discord
ok, out, err = run("velo_spicy_scanner.py", ["--journal"])
print("MR:", "OK" if ok else f"FAIL — {err[:200]}")
print(out.strip() if out else "")

import time; time.sleep(2)

# Momentum scanner + Discord
ok2, out2, err2 = run("momentum_scanner.py", ["--journal"])
print("MOMO:", "OK" if ok2 else f"FAIL — {err2[:200]}")
print(out2.strip() if out2 else "")