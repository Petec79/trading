# Polymarket Bots — Research & Alpha

**Source:** @LunarResearcher (https://x.com/LunarResearcher/status/1906789497937033271)
**Date:** March 30, 2026
**Project:** Paperclip: Polymarket Bots (MID project)
**Status:** SHELVED — tasks created, not yet started

---

## The Opportunity

- **$7.94B** Polymarket volume in February 2026
- **$2.1B** weekly volume in March — new all-time high
- **92% of traders lose money** — top 0.1% extracted $3.7B
- **Top 20 wallets** captured more profit than bottom 13,000 combined
- The window where a $5/month VPS can compete with institutional desks is **closing**

This isn't gambling. It's a math exam. Most people showed up without a calculator.

---

## The Three Core Formulas

### 1. Expected Value (EV) — The Only Filter That Matters
```
EV = P_true × (1 - P_market) - (1 - P_true) × P_market
```
Market says 40%. You believe 60%. Your edge per dollar:
```
EV = 0.60 × 0.60 - 0.40 × 0.40 = $0.20
```
Rule: **EV < 5% → SKIP. No exceptions.** This eliminates 90% of losing trades.

### 2. Kelly Criterion — Position Sizing
```
f* = (p × b - q) / b
where b = (1 - P_market) / P_market
```
Full Kelly = bet 33% of bankroll. **Never do this.**
Use **Quarter Kelly** always — 1/4 of the full Kelly fraction.

With $1,000 bankroll: bet $83. Won't make you rich tomorrow. Won't blow you up either.

### 3. Bayesian Updating — Change Your Mind Correctly
```
P(H|E) = P(E|H) × P(H) / P(E)
```
55% prior on Fed rate cut → data drops → posterior = 88%. Not panic. Math.

---

## The Tools

### Layer 1: Data
| Tool | Stars | What it does |
|------|-------|-------------|
| poly_data (warproxxx) | 646 | 86M+ trades, every wallet, every entry price |
| py-clob-client | 947 | Official Polymarket SDK — read prices, place orders, WebSocket |
| pmxt | — | Unified API for Polymarket + Kalshi + Limitless |
| prediction-market-analysis (Jon-Becker) | — | Data collection + backtesting framework |

### Layer 2: Intelligence
| Tool | Stars | What it does |
|------|-------|-------------|
| polyterm (NYTEMODEONLY) | 32 | 73 terminal screens, whale tracking, insider detection, arb vs Kalshi |
| insider-tracker (pselamy) | 63 | ML whale detection, flagged $35K → $442K before event (Jan 2026) |
| MiroShark | 285 | Multi-agent AI simulation engine — 1000s of AI personas model outcomes |

### Layer 3: Execution
| Tool | Stars | What it does |
|------|-------|-------------|
| poly-maker (warproxxx) | 963 | Market making bot — both sides of book, collects spread, Google Sheets config |
| Polymarket/agents | 2,600 | Official LLM trading agents, RAG support, news sourcing |
| polymarket-copy-trading-bot | — | Low-latency copy trading, mirror top wallets |

### Layer 4: Infrastructure
| Tool | What it does |
|------|-------------|
| Polysights/Insider Finder | 24K users, $2M funding, $25K Polymarket grant — insider signals |
| pmxt Data Archive | Free hourly Parquet snapshots of orderbook + trade data |

---

## The Top Wallets to Study

| Wallet | PnL | Style |
|--------|-----|-------|
| HorizonSplendidView | +$4M | High-frequency crypto/macro, small edges, massive volume |
| beachboy4 | $6.12M in ONE day | Sports — Tottenham + Sunderland matches |
| majorexploiter | +$2.4M March 2026 | Geopolitics/elections only. Never touches crypto or sports |
| CemeterySun | $36.6M volume | Market making — tiny edge × thousands of trades |

What they share: **mathematical edge + automation.**

---

## The 20-Line Claude Brain

```python
import anthropic, json

def claude_probability(market_question, market_price):
    client = anthropic.Anthropic(api_key="sk-ant-...")
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": f"""You are a calibrated prediction market analyst.
Market: {market_question}
Current price: {market_price}
Estimate the TRUE probability (0.00-1.00).
Consider base rates. Penalize extreme confidence.
Return JSON only: {{"probability": 0.XX, "confidence": "high/medium/low"}}"""}]
    )
    return json.loads(response.content[0].text)
```

Pipe poly_data → Claude scores wallets.
Pipe insider-tracker → Claude cross-references with news.
Pipe polyterm whale data → Claude decides.
That's 50 lines of custom code. Everything else is open source.

---

## Security Warning

December 2025: polymarket-copy-trading-bot contained **malware hidden in a dependency**.
- Professional README, working code, real API connections
- Hidden code read .env, extracted private keys, sent to remote server
- 664 malicious repos are on GitHub right now
- 14,285 people downloaded malware before anyone noticed

**Rules:**
1. NEVER use main wallet — dedicated wallet, minimal funds
2. pip list after every install — audit every dependency
3. Repo created after Feb 2026 with 500+ stars = likely star-farmed
4. Use Revoke.cash — NEVER unlimited USDC approvals
5. Start with $100-300. If it works for 2 weeks, scale gradually.

---

## Paperclip Tasks (MID project)

| ID | Task | Priority | Status |
|----|------|----------|--------|
| MID-19 | Research: Top Wallets & Whale Tracking | medium | in_progress |
| MID-20 | Build: Claude Probability Estimator | high | backlog |
| MID-21 | Build: Whale Follower Bot | high | backlog |
| MID-22 | Build: Market Making Bot | medium | backlog |
| MID-23 | Security Audit: Malware Check | high | backlog |
| MID-24 | Paper Trade: 200+ trades before live | high | backlog |

---

## Strategy Recommendation

1. **Start with research** (MID-19) — identify which wallets to follow
2. **Security audit first** (MID-23) — clear tools before any install
3. **Build probability estimator** (MID-20) — core brain for all strategies
4. **Pick ONE**: Whale Follower vs Market Making — different capital requirements
5. **Paper trade 200+ trades** (MID-24) before going live

**Market making** requires more capital but is most sustainable (collects spread, not predicting direction).
**Whale following** requires less capital but needs good wallet selection.

The edge isn't in knowing the tools exist. It's in actually opening terminal.
