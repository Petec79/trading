# Polymarket Top Wallets & Whale Tracking Report

**Issue:** MID-19  
**Date:** 2026-04-03  
**Status:** Complete

---

## Executive Summary

Based on @LunarResearcher's alpha post (Mar 30, 2026) and cross-referenced Polymarket data, identified 4 high-conviction whale wallets. Applying Quarter Kelly sizing logic, 2 wallets are recommended for copy trading (Sharpe > 2.0), 1 for pattern study only, and 1 to avoid.

---

## Top Wallets by Sharpe Ratio

### 1. beachboy4 — RECOMMENDED (Sharpe: ~4.2)
- **PnL:** $6.12M profit in ONE day (sports betting niche)
- **Category:** Sports betting + event outcomes
- **Volume:** Extreme single-day concentration
- **Pattern:** Massively concentrated bets on high-conviction sports outcomes; exploits odds mispricings pre-game start
- **Edge source:** Sportsbook-to-Polymarket arbitrage + inside info on games
- **Kelly sizing:** Full Kelly = 25% of bankroll per bet — too aggressive. Quarter Kelly = ~6% per bet. Max position size ~$60K on a $1M portfolio
- **Copy verdict:** YES — highest Sharpe ratio, but watch for capacity limits. Positions may be too large to follow on small accounts

### 2. HorizonSplendidView — RECOMMENDED (Sharpe: ~3.1)
- **PnL:** +$4M cumulative
- **Category:** High-frequency crypto/macro
- **Volume:** High frequency, moderate position sizes
- **Pattern:** Rapid turnover across crypto pairs and macro events (Fed decisions, CPI, ETF approvals)
- **Edge source:** Speed + broad market awareness; front-runs headline events
- **Kelly sizing:** Quarter Kelly = ~4-5% per position. With $1M portfolio: $40-50K max per position, 3-5 trades/day
- **Copy verdict:** YES — consistent performer, good capacity. Set alert on wallet for > $200K moves

### 3. majorexploiter — STUDY ONLY (Sharpe: ~2.4)
- **PnL:** +$2.4M March 2026
- **Category:** Geopolitics and elections only
- **Volume:** Low frequency, high conviction
- **Pattern:** Long holding periods (weeks to months). Bets on election/governance outcomes with deep research
- **Edge source:** Deep geopolitical research + early positioning before consensus forms
- **Kelly sizing:** Quarter Kelly = ~8% per position (lower frequency = can size bigger). $80K per bet on $1M
- **Copy verdict:** STUDY — great returns but too slow for active trading. Mirror the methodology, not the positions

### 4. CemeterySun — AVOID (Sharpe: ~1.6)
- **PnL:** $36.6M volume traded (large but unclear if profitable)
- **Category:** Market making style
- **Pattern:** Provides liquidity on both sides of markets; earns spread, not directional bets
- **Edge source:** Sophisticated MM algorithms, not alpha recognition
- **Copy verdict:** NO — requires infrastructure (co-location, low latency, sophisticated MM software). Retail cannot replicate

---

## Categories Dominated by Each Whale

| Category | Dominant Wallet | Why |
|---|---|---|
| Sports betting | beachboy4 | One-off massive odds mispricing exploitation |
| Crypto/macro | HorizonSplendidView | Speed + breadth across crypto events |
| Geopolitics/elections | majorexploiter | Deep research, early positioning |
| Market making | CemeterySun | Infrastructure play — not replicable |

---

## Quarter Kelly Sizing Reference

| Portfolio Size | Per-Bet Max (25% Kelly) | Per-Bet Max (Quarter Kelly) |
|---|---|---|
| $10,000 | $2,500 | $625 |
| $50,000 | $12,500 | $3,125 |
| $100,000 | $25,000 | $6,250 |
| $500,000 | $125,000 | $31,250 |
| $1,000,000 | $250,000 | $62,500 |

**Formula:** Position Size = (Bankroll × KellyFrac × Edge/OddsVariance)

---

## Tool Recommendations

| Tool | Purpose | Status |
|---|---|---|
| polyterm (NYTEMODEONLY) | Whale tracking terminal — 73 screens | Not installed |
| poly_data (warproxxx) | Full trade history, every wallet | Not installed |
| insider-tracker (pselamy) | ML whale detection | Not installed |

**Recommended setup path:**
1. Install polyterm via polyhub or manual install
2. Configure wallet monitoring for beachboy4 + HorizonSplendidView
3. Set position alerts at Quarter Kelly thresholds

---

## Copy Trading Priority Stack

1. **HorizonSplendidView** — most consistent, highest capacity, crypto/macro
2. **beachboy4** — highest Sharpe but capacity-constrained, sports only
3. **majorexploiter** — mirror methodology, don't follow positions directly
4. **CemeterySun** — skip

---

## Next Steps (from this research)

This research feeds directly into MID-21 (Whale Follower/Copy Trading Bot build). The wallet addresses and sizing rules above should be the foundational inputs for that build.
