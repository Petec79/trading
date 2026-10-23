# Momentum v2 — Spicy momentum breakout (paper only)

Rebuild of the momentum bot from @spicyofc's *Momentum Strategy Guide*
(updated 11 April 2026). It replaces the momentum `--journal` path in
`scanner/momentum_scanner.py`, which logged nothing (wrong field names), had
stop and target inverted, booked a fixed +1.5R on every target hit, and ran
every 30 minutes on 5m candles. That scanner still produces its report, but it
no longer writes trades.

**Paper only.** Nothing here sends orders to an exchange.

## What it does, rule by rule

| Guide rule | Page | Implementation |
|---|---|---|
| 1-minute chart | p46, ch.7 | Wakes 3s after every minute, uses **closed** 1m candles only |
| Coins: alt perps, top gainers → longs, worst losers → shorts, hot by trade count | p31-33, p58 | Binance USDT-M crypto perps (no stock/commodity perps, no majors), refreshed every 15 min. "Hot" = top 10 by trades in the last 5 minutes (Orion's TRD 5M), counted from Binance 1m candles |
| ≥ $100k / 1 min volume | p58 | Checked at signal time on the last hour's average |
| Levels: previous 1h / 4h / day / week high (longs) or low (shorts) | p45, p48 | Previous **completed** candle of each timeframe; biggest level wins |
| Entry trigger: 1 candle **close** through the level | p49-51 | A candle that closes through it after one that didn't. One attempt per level |
| Stop at the relevant swing low / high (3-candle swing) | p42, p54, p77 | Most recent confirmed 3-candle swing at least 1% beyond the level (p77: the author skips swings closer than 1%) |
| Stop < 3% → limit order on the broken level, ≥ 3% → market order | p52-53 | Limit fills only if price comes back to the level (30-min expiry; cancelled if it runs to target first). Market fills at the next open + slippage |
| Quality: (1) slow grind, not a fast spike (2) volume increasing (3) ≥ 2h staircase | p36, p38, p60 | Scored 0-3. Needs ≥ 2/3 **and** the staircase (p95 tip) |
| 3/3 → next level; anything less → 1R | p56, ch.7 | 3/3 targets the next resistance/support (other HTF levels + 1h swings, 7 days). Nothing left beyond (ATH-style) → no TP, trail stop to each new swing (p57 #3) |
| Evolving R rule: at +0.9R move stop to +0.1R | p79 | Yes |
| Cut at the 2-hour mark | p66, p68, p82 | Yes |
| No +0.1R in the first 15 min → cut | p75 tip | Yes (can be switched off: `early_cut_enabled`) |
| Fees | p52 | Maker 0.02%, taker 0.05%, slippage 0.05%. Journal R is **net** of all costs |

Not automated: the "next big sell wall" target (needs the order book) and
risk-sizing by regime (p7). The bot works in R units.

### Where the guide is a judgement call
"Does this look like a staircase?" can't be coded directly, so `config.py`
turns each judgement into a number, marked `ASSUMPTION`: efficiency ratio of
the 5m closes over 2h ≥ 0.30 and over the last hour ≥ 0.25, swing steps ≥ 60%
in the trade direction, last-hour volume ≥ 1.15× the hour before, and a spike
filter. **These are the first things to tune with the backtest.**

## Run it on the VPS

Put `momentum_v2/` next to `journal/` and `scanner/` in `/home/openclaw/workspace`.

```bash
cd /home/openclaw/workspace
python3 momentum_v2/tests/test_engine.py          # 26 tests, synthetic data
python3 -m momentum_v2.live --once --no-journal   # one dry cycle against live Binance data
```

Run as a service (user `openclaw`):

```bash
mkdir -p ~/.config/systemd/user
cp momentum_v2/deploy/momentum-v2.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now momentum-v2
journalctl --user -u momentum-v2 -f
```

Discord posts are optional. Add the (new, rotated) webhook URLs to `~/.hermes/.env`:

```
DISCORD_SIGNALS_WEBHOOK=https://discord.com/api/webhooks/...
DISCORD_JOURNAL_WEBHOOK=https://discord.com/api/webhooks/...
```

Trades land in `journal/journal.db` with `scanner = 'momentum_v2'`. The old
hourly exit monitor skips them (`SELF_MANAGED_SCANNERS` in `journal.py`),
because this bot manages its own exits candle by candle. Live state (open
position or pending order per coin) is saved in `momentum_v2/data/live_state.json`,
so a restart picks up where it left off.

## Backtest

```bash
python3 -m momentum_v2.backtest --symbols WIFUSDT,PEPEUSDT,FARTCOINUSDT,VIRTUALUSDT \
    --start 2026-08-01 --end 2026-10-20 --out momentum_v2/data/trades.csv
# try a variation:
python3 -m momentum_v2.backtest ... --set early_cut_enabled=False --set staircase_min_er_2h=0.25
```

The backtest uses the same engine as the live bot, with pessimistic fills:
the stop wins any candle that touches both stop and target, a limit order
needs a real retest of the level, and no target can fill on the limit's
entry candle. It prints win rate, average and total net R, profit factor and
max drawdown, broken down by score, exit reason, entry type, level, side,
target type and coin. Pick coins that were hot during the test window, because the
live bot only trades the day's movers.

Caveat: the backtest trades every coin you give it, every day. The live bot
only trades a coin while it is a top mover, and caps open positions at 5.
