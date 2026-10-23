# Trading journal (v2)

A SQLite file, `journal/journal.db`, written by the two v2 paper bots (`momentum_v2`, `mean_reversion_v2`) and by you when you log your own trades. Every report computes its numbers in **`stats.py`**, so the reports always agree with each other.

## Fresh start

The old journal held only trades from the broken v1 bots, with made-up R values. Delete it when you switch to v2, **after** removing the old scanner cron lines:

```bash
cd /home/openclaw/workspace && bash journal/reset_journal.sh
```

The script checks first and stops if:
- any old scanner job is still in cron
- a v2 bot has an open paper trade

If those pass, it shows what's in the old file and asks you to type `DELETE`. It then creates the empty journal and restarts the bots. **Deletion is permanent.**

## How the numbers are counted
- **A trade** is a closed result: a win, loss or breakeven. Unfilled or cancelled limit orders are counted separately.
- **Expectancy** is total R ÷ all closed trades, breakevens included.
- **Win rate** is shown with and without breakevens. The bots move stops to ~breakeven at +0.9R, so breakevens are common.
- **Dates** use the time a trade **exited**, parsed properly, so Sunday trades now count in their week.
- **R** is net of fees and slippage. **$** is R × $100 risk, or `ALERT_ACCOUNT` × `ALERT_RISK_PCT`.

## Reports

```bash
python3 journal/performance_report.py                     # all time, by bot / setup / side / score / exit reason
python3 journal/performance_report.py --weeks 1 --telegram
python3 journal/weekly_report.py --send                   # Telegram + Discord; cron: 5 8 * * 1
python3 pico-bot/journal/performance_report.py --telegram # PICO section of Midas's standup (same numbers)
python3 -m momentum_v2.news_report                        # fast spikes: news verdict / hype vs result
```

## Logging your own trades

When the bot's paper trade fills, the Telegram fill message includes a ready-made command with the signal ID, stop and size filled in. Put in **your** fill price:

```bash
python3 journal/journal.py manual-open --signal MR2-20261023-001 --coin PEPE --direction short \
    --entry 0.998 --stop 1.048 --size-usd 2000 --venue breakout
python3 journal/journal.py manual-close --id 1 --exit 0.951 --fees 2.0
python3 journal/journal.py manual          # yours vs the bot's paper result, entry slippage
```

Your R is measured against **your** stop, net of the fees you enter. The weekly report compares your results with the bot's paper results for the same signals. A positive slippage figure means you got a worse price than the bot's paper fill.
