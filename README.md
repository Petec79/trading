# trading

Trading bots, scanners, strategies and journal scripts.

## Layout

- `journal/` — paper-trading journal: `Journal` class (`journal.py`), performance report (`performance_report.py`), weekly report (`weekly_report.py`), standup section (`standup_report.py`).
- `strategies/`, `scanner/`, `polymarket-bots/`, `pico-bot/`, `ibiza-concierge/` and other workspace subfolders live on the VPS, not in this repo.

## Not in this repo

- `journal.db` and `trades.db` (SQLite databases) — large, binary, host-local.
- Live trade execution paths — paper-only; the system is documented as a paper journal in `journal.py`.
- VPS-level config (`~/.hermes/.env`, credentials, etc.) — never committed.

## Rules

- Patches only via PR.
- No secrets in any committed file (covered by `.gitignore` and the secrets-scan step in the agent workflow).
