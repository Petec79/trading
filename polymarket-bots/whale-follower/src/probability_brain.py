#!/usr/bin/env python3
"""
Claude Probability Estimation Brain (20-line core)
Takes a Polymarket question + market price → returns true probability + EV verdict.

Usage:
    from src.probability_brain import estimate

    prob, confidence, verdict = estimate(
        question="Will BTC close above $100k by Dec 2025?",
        market_price=0.62,
        min_ev=0.05,
    )
    # prob: 0.0-1.0, confidence: low/medium/high, verdict: PROCEED|SKIP
"""
import os
import json
import anthropic
from dataclasses import dataclass

MIN_EV_DEFAULT = 0.05


@dataclass
class ProbabilityEstimate:
    true_prob: float       # Estimated true probability (0.0-1.0)
    confidence: str        # "low" | "medium" | "high"
    ev: float              # Expected value
    verdict: str           # "PROCEED" | "SKIP"
    reasoning: str          # Brief explanation


def calc_ev(true_prob: float, market_price: float) -> float:
    """EV = P_true × (1 - P_market) - (1 - P_true) × P_market"""
    return true_prob * (1 - market_price) - (1 - true_prob) * market_price


def bayesian_update(prior: float, likelihood: float, marginal: float) -> float:
    """P(H|E) = P(E|H) × P(H) / P(E)"""
    if marginal <= 0:
        return prior
    return (likelihood * prior) / marginal


def estimate(
    question: str,
    market_price: float,
    min_ev: float = MIN_EV_DEFAULT,
    model: str = "claude-sonnet-4-20250514",
) -> ProbabilityEstimate:
    """
    Query Claude for true probability, calculate EV, return verdict.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ProbabilityEstimate(
            true_prob=market_price,
            confidence="low",
            ev=0.0,
            verdict="SKIP",
            reasoning="No ANTHROPIC_API_KEY — cannot estimate",
        )

    prompt = f"""You are a calibrated prediction market analyst.
Market question: {question}
Current market price: {market_price:.2f} (probability of YES, 0.00-1.00)

Estimate the TRUE probability of YES (0.00 to 1.00).
Consider: base rates, historical accuracy of similar markets, recent news.
Penalize extreme confidence. Return ONLY valid JSON:
{{"probability": 0.XX, "confidence": "low|medium|high", "reasoning": "..."}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        true_prob = float(data["probability"])
        confidence = data.get("confidence", "medium")
        reasoning = data.get("reasoning", "")
    except Exception as e:
        return ProbabilityEstimate(
            true_prob=market_price,
            confidence="low",
            ev=0.0,
            verdict="SKIP",
            reasoning=f"Claude error: {e}",
        )

    # Bayesian update: blend Claude estimate with market price (prior)
    # Use market price as prior, Claude output as likelihood
    posterior = bayesian_update(market_price, true_prob, (true_prob + market_price) / 2)
    true_prob = round(posterior, 4)

    ev = calc_ev(true_prob, market_price)
    verdict = "PROCEED" if ev >= min_ev else "SKIP"

    return ProbabilityEstimate(
        true_prob=true_prob,
        confidence=confidence,
        ev=round(ev, 4),
        verdict=verdict,
        reasoning=reasoning,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m src.probability_brain '<question>' <market_price>")
        sys.exit(1)

    question = sys.argv[1]
    market_price = float(sys.argv[2])

    result = estimate(question, market_price)

    print(f"\n{'='*50}")
    print(f"  Question: {question[:60]}")
    print(f"  Market Price: {market_price:.2f}")
    print(f"  True Probability: {result.true_prob:.2f} ({result.confidence})")
    print(f"  Expected Value: {result.ev:.4f}")
    print(f"  Verdict: {result.verdict}")
    print(f"  Reasoning: {result.reasoning[:80]}")
    print(f"{'='*50}")
