# Mean Reversion Trading Strategy — Rules Summary
## Based on @spicyofc "Mean Reversion Trading Strategy Guide"
> Educational purposes only. Not financial advice.

---

## The 3 Reversal Setups

### Setup 1: Failed Breakouts

**Conditions:**
- Price must be IN A RANGE (sideways)
- Volume CANNOT be increasing

**Entry:** After 1 candle closes BACK INSIDE the range
- Long: price failed to break resistance, now closing back inside
- Short: price failed to break support, now closing back inside

**Target:** Opposite side of the range

**Stoploss:** Next S/R level OR ~1R in the opposite direction

---

### Setup 2: Fast Spike Reversals

**Requirements:**
- Price "spikes" a level (steep slope, large move relative to recent avg, high volume)
- Price FAILS to break through the level — creates a swing high/low
- 1 candle closes BELOW swing low (bearish BOS) or ABOVE swing high (bullish BOS)

**Order Type:**
- Spike size > 1.5% → **MARKET order**
- Spike size < 1.5% → **LIMIT order** on the broken level

**Target:** Origin of the spike (where it started)

**Stoploss:** Extreme point of the spike

**AVOID if:**
- Spike was caused by a news event
- Spike was correlated with a BTC move at the same time
- Spike hasn't hit a key S/R level yet (wait for a level to be tested)

**Quality:**
- Structure broken in < 15 min → HIGH quality
- Structure broken in < 30 min → AVERAGE
- Structure broken in > 90 min → WARNING, skip

---

### Setup 3: Partial Fill Reversals (Top Tick / Bottom Tick)

**Requirements:**
- Price "top ticks" — hits EXACT same price as prior swing high (within 2 ticks, not just "similar")
- Higher-than-average volume on the top tick candle

**Entry:** After the NEXT candle closes in the OPPOSITE direction
- Confirms market orders failed to break through the wall of limit orders

**Target:** Recent swing low (long) / swing high (short)

**Stoploss:** 1R equidistant from entry to target

**Order Type:** MARKET order only (must react to confirmation)

---

## Reversal Regime Check (Easy Mode)

At least **2 out of 3** variables must be present for a good reversal environment:

| # | Variable | Description |
|---|----------|-------------|
| 1 | **Fast Spike OR Range** | Price either spikes into the level OR is stuck in a range |
| 2 | **Volume NOT Increasing** | Decreasing or flat volume — less fuel for breakouts |
| 3 | **Sideways Price Action** | In-range bouncing — rewards reversals, punishes breakouts |

### Quality Scoring

| Score | Reversal? | Strategy |
|-------|-----------|----------|
| **3/3** | All align — high confidence | Full R target, be ambitious |
| **2/3** | Good regime | Standard 1R target |
| **1/3** | Low confidence | Conservative, skip if unsure |
| **0/3** | **AVOID** | Momentum is dominant here |

---

## Why Reversals Work

Trading is a **zero-sum game**:
- When you bet on a reversal and WIN, you took money from breakout traders who got stopped out
- When you bet on a reversal and LOSE, you lost to breakout traders

**Reversal-friendly conditions = breakout-unfriendly conditions.**

---

## Failed Breakouts — Why They Work

When price is in a range:
1. Support/resistance levels are **holding price** — breakout traders keep getting stopped out
2. These stoploss orders REST on the opposite side of the range
3. Your take-profit (a limit order) will be filled by those stopped-out breakout traders
4. The orderbook is thick at the range edges — it takes a lot of pressure to break through

---

## Fast Spikes — Why They Work

A "fast spike" creates an **imbalance in the orderbook**:
1. Large market orders execute rapidly, clearing through limit orders
2. This leaves a "thin" or "empty" orderbook on the side where the spike came from
3. It takes LITTLE pressure to push price back down
4. The "origin" of the spike is where the orderbook is more evenly distributed — natural target

**AVOID news-driven or BTC-correlated spikes** — these have "smart money" behind them, not random gambler traders.

---

## Partial Fills — Why They Work

A "partial fill" means:
1. Large market orders hit a wall of limit orders
2. Only PART of the market order was filled — leftover limit orders remain
3. No one is left to continue pushing in that direction
4. Market players on the other side now control the price

Only trade partial fills at **relevant swing highs/lows** — not random price levels. Higher volume on the top tick = higher quality.

---

## Common Mistakes

| Mistake | Why It's Bad |
|---------|-------------|
| Trading reversals in trending markets | Breakout traders will run over you |
| Trading reversals on news spikes | "Smart money" is behind it |
| Trading reversals when volume is increasing | Breakout traders have momentum |
| Taking all failed breakouts without checking volume | You'll get chopped up by fees |
| Ignoring the "2/3 rule" for regime | Trading the wrong style for conditions |

---

## Quick Reference Cheat Sheet

```
REVERSAL REGIME CHECK (need 2/3):
  □ Fast spike OR range price action?
  □ Volume NOT increasing?
  □ Sideways/in-range?

FAILED BREAKOUT:
  Price in range + vol not increasing
  → 1 candle closes back inside range
  → Target: opposite side of range
  → Stop: next S/R level or 1R

FAST SPIKE REVERSAL:
  Spike + failed + break of structure
  → Spike > 1.5%: MARKET order
  → Spike < 1.5%: LIMIT order
  → Target: origin of spike
  → Stop: extreme of spike
  → AVOID: news spikes, BTC-correlated spikes

PARTIAL FILL (TOP TICK):
  Exact same price as prior swing high + high volume
  → Next candle closes opposite direction
  → MARKET order
  → Target: recent swing low
  → Stop: 1R

SCORE 3/3 → ambitious target
SCORE 2/3 → 1R target
SCORE 1/3 → conservative
SCORE 0/3 → DO NOT TRADE
```

---

## Timeframe
- Author trades exclusively on the **1-minute timeframe**
- Portable to any timeframe — range detection lookback and swing strength may need adjustment

---

## Which Coins to Trade
- Altcoin perpetual futures (ideally $100k+/min volume)
- Scanners: Orion Terminal (Trade Count), Velo (Top Gainers / Worst Losers)
- Look for coins in choppy/sideways regimes with decreasing volume
- Avoid trending coins with increasing volume — trade momentum there instead

---

## Files
- `mean-reversion-strategy.pine` — TradingView Pine Script v5 indicator
- `mean-reversion-rules.md` — This summary document
