#!/usr/bin/env python3
"""
Level 1: First Trading Signal in ~50 Lines
Based on zostaff's guide - LLM for Trading

Cost: 1,000 news articles through GPT-4o-mini = $0.05
"""
import os
import requests

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = "gpt-4o-mini"  # Cheap: $0.15/M input, $0.60/M output
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

SYSTEM_PROMPT = """You are a senior financial analyst. 
Analyze news and predict market direction.
Respond ONLY with JSON: {"signal": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "reason": "brief explanation"}"""

ANALYST_PROMPT = """Analyze this news and predict its market impact:

{news}

Consider:
- Is this bullish or bearish for the asset?
- How significant is this information?
- What's the confidence level?

Respond with structured JSON."""

def fetch_news(query="Bitcoin", limit=10):
    """Fetch latest news (using free NewsAPI or similar)."""
    # Placeholder - replace with actual news API
    sample_news = [
        "Bitcoin surges past $85,000 amid institutional buying",
        "Major bank announces cryptocurrency trading desk",
        "Regulatory concerns rise as Bitcoin mining energy usage increases",
        "El Salvador's Bitcoin holdings now worth $1 billion",
        "SEC delays Bitcoin ETF decision again",
    ]
    return sample_news[:limit]

def analyze_news(news_article: str) -> dict:
    """Send news to LLM and get trading signal."""
    if not OPENAI_API_KEY:
        return {"signal": "HOLD", "confidence": 0.5, "reason": "No API key"}
    
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ANALYST_PROMPT.format(news=news_article)}
        ],
        "temperature": 0.3
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.ok:
            result = resp.json()["choices"][0]["message"]["content"]
            # Parse JSON from response
            import json
            return json.loads(result)
    except Exception as e:
        return {"signal": "ERROR", "confidence": 0, "reason": str(e)}

def generate_signal(news_articles: list) -> dict:
    """Analyze multiple news items and generate overall signal."""
    signals = []
    
    for article in news_articles:
        result = analyze_news(article)
        print(f"  {result.get('signal', 'ERROR'):5} ({result.get('confidence', 0):.2f}) {article[:50]}...")
        signals.append(result)
    
    # Aggregate signals
    buy_count = sum(1 for s in signals if s.get('signal') == 'BUY')
    sell_count = sum(1 for s in signals if s.get('signal') == 'SELL')
    avg_confidence = sum(s.get('confidence', 0) for s in signals) / len(signals)
    
    if buy_count > sell_count:
        return {"signal": "BUY", "confidence": avg_confidence, "details": signals}
    elif sell_count > buy_count:
        return {"signal": "SELL", "confidence": avg_confidence, "details": signals}
    else:
        return {"signal": "HOLD", "confidence": avg_confidence, "details": signals}

# === LEVEL 1: CHAIN-OF-THOUGHT FOR COMPLEX NEWS ===
COMPLEX_PROMPT = """When news is ambiguous, reason step by step:

1. What happened?
2. Who is affected?
3. What's the market sentiment?
4. What's the likely price impact?
5. What's my final signal?

News: {news}

Provide structured reasoning then final signal."""

# === MAIN ===
if __name__ == "__main__":
    print("=" * 60)
    print("LEVEL 1: First Trading Signal (50 lines)")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Cost: ~$0.05 per 1,000 articles")
    print("=" * 60)
    
    news = fetch_news("Bitcoin", limit=5)
    print(f"\nAnalyzing {len(news)} news articles...\n")
    
    signal = generate_signal(news)
    
    print(f"\n{'='*60}")
    print(f"FINAL SIGNAL: {signal['signal']}")
    print(f"CONFIDENCE: {signal['confidence']:.2f}")
    print(f"{'='*60}")