#!/usr/bin/env python3
"""
Weekly journal report (Telegram-ready). Run Monday mornings:

    python3 journal/weekly_report.py            # print
    python3 journal/weekly_report.py --send     # print + Telegram + Discord

cron:  5 8 * * 1  cd /home/openclaw/workspace && /usr/bin/python3 journal/weekly_report.py --send
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from journal import Journal  # noqa: E402


def build_report() -> str:
    return Journal().format_weekly_report(weeks_back=4)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    text = build_report()
    print(text)
    if a.send:
        from momentum_v2.alerts import Notifier
        Notifier("journal").send(text)
