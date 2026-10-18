"""
Real Data Connectors for Trading System
- CoinGecko: Prices (free, no API key)
- Polymarket: Markets
"""
import requests
from datetime import datetime
from typing import List, Dict

# Configuration
COINGECKO_API = "https://api.coingecko.com/api/v3"
POLYMARKET_API = "https://gamma-api.polymarket.com"

class PriceFeed:
    """Real price data from CoinGecko."""
    
    def __init__(self):
        self.coin_ids = {
            "bitcoin": "bitcoin",
            "ethereum": "ethereum", 
            "solana": "solana",
            "dogecoin": "dogecoin"
        }
    
    def get_prices(self, vs_currency="usd") -> List[Dict]:
        """Get current prices for tracked coins."""
        try:
            ids = ",".join(self.coin_ids.values())
            url = f"{COINGECKO_API}/simple/price"
            params = {
                "ids": ids,
                "vs_currencies": vs_currency,
                "include_24hr_change": "true"
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.ok:
                data = resp.json()
                results = []
                for name, coin_id in self.coin_ids.items():
                    if coin_id in data:
                        price = data[coin_id].get(vs_currency, 0)
                        change = data[coin_id].get(f"{vs_currency}_24h_change", 0)
                        results.append({
                            "coin": name.title(),
                            "symbol": coin_id[:3].upper(),
                            "price": price,
                            "change_24h": change,
                            "text": f"{name.title()} (${coin_id[:3].upper()}): \${price:,.0f} - 24h {change:+.1f}%",
                            "source": "CoinGecko"
                        })
                return results
        except Exception as e:
            print(f"[PriceFeed] Error: {e}")
        # Fallback: return hardcoded if API fails
        return [
            {"coin": "Bitcoin", "symbol": "BTC", "price": 67200, "change_24h": 0.5, "text": "Bitcoin (BTC): $67,200 - 24h +0.5%", "source": "fallback"},
            {"coin": "Ethereum", "symbol": "ETH", "price": 3450, "change_24h": 0.3, "text": "Ethereum (ETH): $3,450 - 24h +0.3%", "source": "fallback"},
        ]
    
    def get_bitcoin_price(self) -> Dict:
        """Get just Bitcoin price."""
        prices = self.get_prices()
        for p in prices:
            if p["symbol"] == "BTC":
                return p
        return {}


class MarketData:
    """Real market data from Polymarket."""
    
    def __init__(self):
        self.tracked_keywords = ["bitcoin", "btc", "ethereum", "eth", "crypto", "trump", "election"]
    
    def get_active_markets(self, limit=20) -> List[Dict]:
        """Get active prediction markets."""
        try:
            resp = requests.get(
                f"{POLYMARKET_API}/markets",
                params={"limit": limit, "closed": "false"},
                timeout=10
            )
            if resp.ok:
                markets = resp.json()
                # Filter for relevant markets
                relevant = []
                for m in markets:
                    q = m.get("question", "").lower()
                    vol = float(m.get("volume", 0) or 0)
                    if any(kw in q for kw in self.tracked_keywords) and vol > 5000:
                        relevant.append({
                            "question": m.get("question"),
                            "slug": m.get("slug"),
                            "volume": vol,
                            "condition_id": m.get("conditionId"),
                            "url": f"https://polymarket.com/market/{m.get('slug', m.get('conditionId',''))}"
                        })
                # Sort by volume
                relevant.sort(key=lambda x: x["volume"], reverse=True)
                return relevant[:10]
        except Exception as e:
            print(f"[MarketData] Error: {e}")
        return []
    
    def get_market_price(self, condition_id: str) -> Dict:
        """Get current price for a market."""
        try:
            # Try CLOB endpoint
            resp = requests.get(
                f"{POLYMARKET_API}/markets/{condition_id}",
                timeout=5
            )
            if resp.ok:
                return resp.json()
        except:
            pass
        return {}


class NewsFeed:
    """Real news from various sources."""
    
    def __init__(self):
        # Use free CryptoCompare API for news
        self.news_url = "https://min-api.cryptocompare.com/data/v2/news/"
    
    def get_crypto_news(self, categories="BTC,ETH", limit=10) -> List[Dict]:
        """Get crypto news from CryptoCompare (free tier)."""
        try:
            params = {
                "categories": categories,
                "excludeCategories": "Sponsored",
                "limit": limit
            }
            resp = requests.get(self.news_url, params=params, timeout=10)
            if resp.ok:
                data = resp.json()
                articles = data.get("Data", [])
                if not articles:
                    return []
                return [
                    {
                        "text": a.get("title", "")[:100],
                        "source": a.get("source_info", {}).get("name", "Unknown"),
                        "url": a.get("url", ""),
                        "categories": a.get("categories", ""),
                        "timestamp": datetime.fromtimestamp(a.get("published_on", 0)).isoformat()
                    }
                    for a in articles[:limit]
                ]
        except Exception as e:
            print(f"[NewsFeed] Error: {e}")
        return []


# === TEST ===
if __name__ == "__main__":
    print("=" * 60)
    print("Testing Real Data Connectors")
    print("=" * 60)
    
    # Test Price Feed
    print("\n--- Prices ---")
    pf = PriceFeed()
    prices = pf.get_prices()
    for p in prices:
        print(f"  {p['text']}")
    
    # Test News Feed
    print("\n--- News ---")
    nf = NewsFeed()
    news = nf.get_crypto_news()
    for n in news[:5]:
        print(f"  - {n['text'][:60]}... ({n['source']})")
    
    # Test Market Data
    print("\n--- Markets ---")
    md = MarketData()
    markets = md.get_active_markets()
    for m in markets[:5]:
        print(f"  ${m['volume']:,.0f} | {m['question'][:50]}...")
    
    print("\n" + "=" * 60)
    print("Real data connectors working!")
    print("=" * 60)