"""
Challenge Module — Validates whale signals before execution.
Each signal gets challenged up to MAX_CHALLENGE_ROUNDS (default 3).
A signal is accepted if it survives all challenge rounds.
"""
import time
import requests
from typing import Optional
from .config import GAMMA_API, MIN_EV_THRESHOLD, MAX_CHALLENGE_ROUNDS
from .scanner import get_market_info, get_market_orderbook


class ChallengeResult:
    ACCEPT = "accept"
    REJECT = "reject"
    SKIP = "skip"      # No opinion, let the whale signal stand


def get_true_probability_via_claude(question: str, market_price: float) -> Optional[float]:
    """
    Use Claude to estimate true probability for a market question.
    Falls back to market price if API is unavailable.
    Returns probability in range [0.0, 1.0] or None on failure.
    """
    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": f"""You are a calibrated prediction market analyst.
Market question: {question}
Current market price: {market_price:.2f} (this is the probability of YES, expressed as a decimal 0-1)
Estimate the TRUE probability of YES (0.00 to 1.00).
Consider: base rates, historical accuracy of similar markets, recent relevant news.
Penalize extreme confidence.
Return ONLY a JSON object: {{"probability": 0.XX}}"""
            }]
        )
        import json
        text = response.content[0].text.strip()
        data = json.loads(text)
        return float(data["probability"])
    except Exception as e:
        print(f"[challenge] Claude probability error: {e}")
        return None


def calc_ev(true_prob: float, market_price: float) -> float:
    """
    Calculate expected value of a bet.
    EV = P_true * (1 - P_market) - (1 - P_true) * P_market
    """
    p_market = market_price
    ev = true_prob * (1 - p_market) - (1 - true_prob) * p_market
    return ev


def challenge_signal(signal: dict, round_num: int) -> tuple[str, str]:
    """
    Challenge a whale signal for one round.
    Returns (verdict: ChallengeResult, reason: str).
    Verdict is ACCEPT if we should follow the whale, REJECT if we should skip.
    """
    slug = signal["slug"]
    question = signal["question"]
    whale_side = signal["whale_side"]
    current_price = signal["current_price"]

    # ── Challenge 1: Market still active? ─────────────────────────────────────
    market_info = get_market_info(slug)
    if not market_info:
        return ChallengeResult.REJECT, f"Market not found for {slug}"
    end_date = market_info.get("endDate", "")
    if end_date:
        from datetime import datetime, timezone
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > end_dt:
                return ChallengeResult.REJECT, f"Market already ended: {end_date}"
        except Exception:
            pass

    # ── Challenge 2: EV check via Claude ──────────────────────────────────────
    true_prob = get_true_probability_via_claude(question, current_price)
    if true_prob is not None:
        ev = calc_ev(true_prob, current_price)
        if ev < MIN_EV_THRESHOLD:
            return ChallengeResult.REJECT, f"EV too low: {ev:.3f} < {MIN_EV_THRESHOLD}"
        if ev > 0.20:
            reason = f"Strong EV: {ev:.3f} — accepting"
        else:
            reason = f"EV acceptable: {ev:.3f}"
    else:
        # No Claude — accept if price is not extreme
        if current_price < 0.05 or current_price > 0.95:
            return ChallengeResult.REJECT, f"Price too extreme without external validation: {current_price:.2f}"
        reason = "No external validation — accepting on whale signal alone"

    # ── Challenge 3: Liquidity check ───────────────────────────────────────────
    orderbook = get_market_orderbook(slug)
    if orderbook:
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if not asks and not bids:
            return ChallengeResult.REJECT, "No liquidity in orderbook"
        # Check if we can get at least $100 of fill at current price
        # (just a rough check — real implementation would walk the book)
        if whale_side == "BUY" and asks:
            best_ask = float(asks[0][0])
            if abs(best_ask - current_price) / current_price > 0.05:
                return ChallengeResult.REJECT, f"Price slippage too high: {best_ask:.3f} vs {current_price:.3f}"
        elif whale_side == "SELL" and bids:
            best_bid = float(bids[0][0])
            if abs(best_bid - current_price) / current_price > 0.05:
                return ChallengeResult.REJECT, f"Price slippage too high: {best_bid:.3f} vs {current_price:.3f}"

    return ChallengeResult.ACCEPT, reason


def challenge(signal: dict) -> tuple[bool, str]:
    """
    Challenge a whale signal through MAX_CHALLENGE_ROUNDS.
    Returns (accepted: bool, reason: str).
    """
    rounds = signal.get("challenge_rounds", MAX_CHALLENGE_ROUNDS)
    reasons = []

    for r in range(1, rounds + 1):
        verdict, reason = challenge_signal(signal, round_num=r)
        reasons.append(f"Round {r}: {verdict} — {reason}")

        if verdict == ChallengeResult.REJECT:
            return False, " | ".join(reasons)
        elif verdict == ChallengeResult.SKIP:
            # No opinion — move to next round
            time.sleep(1)
            continue
        # ACCEPT — continue to next round to confirm
        time.sleep(1)

    return True, " | ".join(reasons)
