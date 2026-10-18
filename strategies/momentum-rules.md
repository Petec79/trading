# Momentum Trading Strategy — Rules Summary
## Based on @spicyofc "Momentum Trading Strategy Guide"
> Educational purposes only. Not financial advice.

---

## The 3 Momentum Criteria

Every momentum trade setup must pass these 3 checks:

| # | Criteria | What It Means |
|---|----------|---------------|
| 1 | **Grindy "Staircase" Approach** | Price making successive higher highs + higher lows (longs) or lower highs + lower lows (shorts) as it approaches the level |
| 2 | **Increasing Volume** | Volume trending above its moving average — shows market heating up |
| 3 | **2+ Hours of Staircase** | At least 2 hours of clean staircase structure before the entry trigger fires |

### Quality Scoring

| Score | Trade? | Target |
|-------|--------|--------|
| **3/3** | All stars aligned — take it | Be ambitious, next resistance level |
| **2/3** | Good trade — take it | Play it safe, 1R target |
| **0-1/3** | **AVOID** | Not a momentum regime |

> **Rule of thumb:** If there is no staircase, do NOT take the momentum trade — even if 2/3 other criteria are met. Staircase is the most important criterion.

---

## Market Regimes — Best vs Worst

### Best for Momentum (Take Trades)
- Price grinding into the level (not spiking)
- Volume consistently increasing over time
- High volatility
- "Staircase" price action — low friction, high margin of error
- On Orion Terminal / Velo: hot coins with high trade counts

### Worst for Momentum (Skip Trades)
- Fast violent spike into the level (clears the orderbook, leaves a "void")
- Volume decreasing over time
- Low volatility / sideways ranging
- Choppy price action with many MA crossovers
- Ranging markets — mean reversion is better here

---

## Entry Rules

### WHERE — Identify the Level
Use **swing highs** (for longs) and **swing lows** (for shorts):
- Previous 1 Hour High/Low
- Previous 4 Hour High/Low
- Previous Day High/Low
- Previous Week High/Low

> The bigger the level, the easier the trade. Daily/weekly levels give more violent reactions.

### WHEN — Entry Trigger
**After 1 candle closes through the level**
- Wait for a candle to definitively break and close beyond the swing high/low
- This confirms price "broke through the wall" of limit orders

### HOW — Order Type
| Distance to SL | Order Type | Where |
|----------------|------------|-------|
| < 3% | **Limit Order** | Placed directly on the broken level |
| > 3% | **Market Order** | Enter immediately after trigger |

> Using market orders when the move is small (< 3%) makes fees too expensive relative to the move.

---

## Stoploss Rules

| Direction | Stoploss Placement |
|-----------|-------------------|
| **Long** | Relevant swing low (below entry) |
| **Short** | Relevant swing high (above entry) |

---

## Target Rules

### Normal Breakouts
- **Target:** The next major resistance level (level-to-level trading)
- Philosophy: Even more pressure is required to break through another level
- Not every trade needs to be a home run — 1R to 1.5R compounds well

### All-Time High Breakouts (Special Case)
When there is literally no resistance above (ATH breakouts):

| Quality | Strategy |
|---------|----------|
| Low quality (score 1-2/3) | Exit at +1R — bank the winner |
| Medium quality | Exit at the next big sell wall (requires orderbook) |
| High quality (score 3/3) | Don't set a fixed TP — trail SL to the next swing low |

---

## Exit Management

### When to Cut a Trade Early
If price is stuck at the entry (not moving in your favor within 15 minutes):
- It may be losing momentum
- Consider cutting and moving on
- Ask: "If someone closed my trade for me and refunded fees, would I re-enter?" If no, exit.

### Evolving Stop Rule
If price moves 0.9R+ in your favor:
- Trail the SL up to +0.1R
- This locks in profit while letting the trade run

---

## Timeframe
- Author (@spicyofc) trades exclusively on the **1-minute timeframe**
- Strategy is portable to any timeframe — just adjust `staircaseHours`
- Higher timeframes = larger moves, fewer signals

---

## Which Coins to Trade
- Altcoin perpetual futures (ideally $100k+/min volume)
- On TradingView: sort by "Top Gainers" for momentum longs
- On Orion Terminal: sort by "Trade Count" to find "hot coins of the day"
- On Velo: Top Gainers (for longs), Worst Losers (for shorts)
- The strategy works in ANY liquid coin — fundamentals don't matter

---

## Common Mistakes (Bad Trade Examples)

| Mistake | Why It's Bad |
|---------|-------------|
| Taking trades in sideways/ranging markets | No momentum — reversals win instead |
| Fast spike into the level | Creates a "void" — price often rejects |
| Ignoring the staircase requirement | Low margin of error |
| Being ambitious with targets on 2/3 score | Should be conservative |
| Holding trades past the regime | Reduce edge the longer you hold |

---

## Quick Reference Cheat Sheet

```
MOMENTUM LONG CHECKLIST:
  □ Staircase price action? (higher highs + higher lows)
  □ Volume increasing?
  □ At least 2h of structure before entry?
  □ 1 candle closed through swing high?
  → Score 3/3 → next resistance
  → Score 2/3 → 1R target
  → Score 0-1/3 → DO NOT TRADE

ORDER TYPE:
  Distance to SL < 3% → LIMIT ORDER
  Distance to SL > 3% → MARKET ORDER

STOPLOSS:
  Long:  at the relevant swing low
  Short: at the relevant swing high
```

---

## Files
- `momentum-strategy.pine` — TradingView Pine Script v5 indicator
- `momentum-rules.md` — This summary document
