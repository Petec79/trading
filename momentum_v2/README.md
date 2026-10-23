# Momentum v2 — Spicy momentum breakout (paper only)

Rebuild of the momentum bot from @spicyofc's *Momentum Strategy Guide*
(updated 11 April 2026). It replaces the momentum `--journal` path in
`scanner/momentum_scanner.py`, which logged nothing (wrong field names), had
stop and target inverted, booked a fixed +1.5R on every target hit, and ran
every 30 minutes on 5m candles. That scanner still produces its report, but it
no longer writes trades.

**Paper only.** Nothing here sends orders to an exchange.

This folder also holds the **mean reversion** rebuild (`mean_reversion.py`,
[section below](#mean-reversion-v2)). Both bots share one engine (`core.py`),
one live runner and one backtester. You choose which one runs with `--strategy`.

## Momentum: what it does, rule by rule

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


---

## Mean reversion v2

Rebuild from @spicyofc's *Mean Reversion Trading Strategy Guide* (part 1 =
theory, part 2 = 50 trade examples). It replaces the `--journal` path in
`scanner/velo_spicy_scanner.py`, which shorted every coin up on the day and
bought every coin down, with ATR-sized stops and targets. None of the guide's
three setups or its regime check were in that code. The scanner still
produces its report, but it no longer writes trades.

Journal scanner name: `mean_reversion_v2`.

### Regime check (part 1 p12, part 2 p7)
| Variable | Good for reversals | Implementation (`ASSUMPTION` values in `MRConfig`) |
|---|---|---|
| Approach into the level | Fast spike, not a slow grind | Last 5 candles moved ≥ 1% and ≥ 5× the average 2h candle, with a candle at ≥ 3× median volume |
| Volume | Flat or decreasing, not increasing | Last hour ≤ 1.10× the hour at the start of the regime and ≤ 1.15× the previous hour (recent data weighted more, part 2 p63) |
| Price action on the left | Sideways range (or a choppy trend in the trade's direction, p84) | ≥ 6 crosses of the 20-MA in 2h and a low efficiency ratio |

- **Scoring:** 3/3 or 2/3 → take. 1/3 → only if the range has lasted 4h+ (part 2 p13). 0/3 → never.
- **Veto:** a 2h staircase against the trade blocks it whatever the score (part 2 bad trades #3-#25).

### The three setups (part 1 chapter 7)
| | Trigger | Entry | Target | Stop |
|---|---|---|---|---|
| **Failed breakout** p94-110 | Candle closes outside the 2h range, then one closes back inside (or a one-candle wick, part 2 p20). Both range edges must have **held price 3 times**. Needs a range **and** volume not increasing. Mildly rising volume is allowed when the range has lasted 4h+ (part 2 #2, #15, #20) | Range ≥ 3% → market, else limit on the failed level | Other side of the range | Next S/R level if it's roughly 1R away, else 1R |
| **Fast spike reversal** p111-129 | Spike into a key level (previous 1h/4h/day/week high or low), close back inside, swing forms, candle closes through it. Skipped if BTC moved ≥ 0.5% the same way, the spike hit no key level, or structure took > 90 min to break | Spike > 1.5% → market, else limit on the broken swing | Origin of the spike | Spike extreme (1R if that gives R:R < 1, part 2 p37) |
| **Partial fill** p130-139 | Candle hits the **exact** price of a prior swing high/low on ≥ 2× average volume; next candle closes the other way | Market | Recent swing (≥ 1%) | 1R (skipped if 1R would sit inside the tick candle) |

On random-walk test data, which has no edge to find, the bot takes about 1 failed
breakout and under 1 partial fill per coin per day, and loses a little after fees. That's the
expected result for noise. An earlier, looser "edges held" rule produced about 8 a day.

### Common rules
- **Skip small moves:** no trade if entry to target is under 1% (part 2 p16, p33).
- **Cancelling limit orders:** cancelled once price covers 90% of the way to target, or after 60 min unfilled (part 2 p22).
- **Exits:** at +0.9R the stop moves to +0.05R ("within 0R to 0.1R", part 2 p35), and trades are cut at 2 hours (part 2 p25).
- **Funding:** skip if funding is above 0.1R and due within 90 min, and only when this side pays (part 1 p92). Live only.
- **Return-bucket bias:** if 80%+ of coins are up, it only takes longs; if 80%+ are down, only shorts (part 1 p81-82). Live only.

### News check for fast spikes (part 1 p122-125) — `news.py`
When a fast spike reversal fires, the bot checks the 2 hours before the spike:
1. **Exchange announcements:** the Bybit and OKX official announcement APIs, and Binance's public announcements page.
2. **News RSS:** CoinDesk, The Block, Cointelegraph and Decrypt.
3. **X via Apify:** only posts from an **allowlist** in `news_sources.json` count. That covers exchanges, security alert accounts, established news accounts and the coin's own account. Bot posts can't get in.

A hit is any item that mentions the ticker and matches one of the guide's "big news" types: listing, delisting, hack or exploit, founder or team trouble, big update, partnership.

The bot also records a **hype score** that never blocks a trade. It takes the latest 50 cashtag posts and measures post rate, the share from bot-like accounts, and duplicate text. This lets you test later whether bot-hyped, news-free spikes fade better.

| `NEWS_MODE` | Behaviour |
|---|---|
| `shadow` (default) | Record the verdict and hype on the trade. Never block |
| `enforce` | Skip the trade on big news, **or if every source failed** |
| `off` | No check |

- **Setup:** X needs `APIFY_TOKEN` in `~/.hermes/.env` and a **paid Apify plan**. On the free plan the main actor only runs in demo mode and can't be called from code. Without it, the X part is skipped and recorded as an error, and exchanges and RSS still run.
- **Cost:** about $0.02–0.05 per fast-spike signal.
- **Evaluate it:** after a few weeks, run `python3 -m momentum_v2.news_report` to compare net R by news verdict and hype level.
- **Maintenance:** check the allowlist handles before relying on them, and add projects' own accounts under `coin_accounts`.

### Not automated
- **News judgement:** `news.py` matches keywords and allowlisted sources. It can't read context the way the author does when he skims X and Velo, so expect some misses and false alarms. Shadow mode is how you measure them.
- **All-time highs/lows (part 2 p49):** the guide says to avoid reversals there. The bot doesn't know where they are.
- **Order book checks:** the order-book-thickness check and round-number levels need the order book.
- **Huge spikes (20%+):** the option to take a smaller target instead of the origin isn't automated.

### Run / backtest
```bash
python3 momentum_v2/tests/test_mean_reversion.py                 # 26 tests
python3 momentum_v2/tests/test_news.py                           # 10 tests (all sources faked)
python3 -m momentum_v2.live --strategy mean_reversion --once --no-journal
cp momentum_v2/deploy/mean-reversion-v2.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now mean-reversion-v2
python3 -m momentum_v2.backtest --strategy mean_reversion \
    --symbols PEPEUSDT,ARCUSDT,FARTCOINUSDT,COOKIEUSDT --start 2026-08-01 --end 2026-10-20
```
The backtest loads BTCUSDT as well, for the correlation check. Funding and
return-bucket bias are live-only, so the backtest leaves them out.
