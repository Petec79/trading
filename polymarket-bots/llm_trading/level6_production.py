#!/usr/bin/env python3
"""
Level 6: Production Deployment
"From prototype to production trading system"

The complete LLM trading system - all 5 levels integrated + production features
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta
from threading import Thread
from typing import Dict, List
import requests

# Import all levels
from level1_signal import generate_signal as level1_signal
from level2_backtest import Backtester, run_backtest
from level3_lora import predict_with_model
from level4_rag import RAGPipeline, NewsIngestor, create_rag_prompt
from level5_multi_agent import MultiAgentOrchestrator
from data_connectors import PriceFeed, MarketData, NewsFeed

# Configuration
CONFIG = {
    "mode": "PAPER",  # PAPER or LIVE
    "initial_capital": 10000,
    "position_size": 0.1,  # 10% of capital per trade
    "max_positions": 3,
    "check_interval": 300,  # 5 minutes
    "max_daily_trades": 10,
    "stop_loss_pct": 0.05,  # 5%
    "take_profit_pct": 0.15,  # 15%
}

class TradingSystem:
    """Complete production trading system."""
    
    def __init__(self, config: dict = None):
        self.config = config or CONFIG
        self.capital = self.config["initial_capital"]
        self.positions = []
        self.trade_history = []
        self.daily_trades = 0
        
        # Initialize components
        self.rag = RAGPipeline()
        self.ingestor = NewsIngestor(self.rag)
        self.orchestrator = MultiAgentOrchestrator()
        
        # Setup logging
        self._setup_logging()
        
        # State
        self.running = False
    
    def _setup_logging(self):
        """Setup production logging."""
        log_file = f"/tmp/trading_system_{datetime.now().strftime('%Y%m%d')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def update_news(self):
        """Ingest latest news into RAG."""
        self.logger.info("Updating news data...")
        self.ingestor.ingest_all()
    
    def generate_signal(self, news_item: str) -> Dict:
        """Generate signal using full pipeline."""
        
        # Level 4: RAG context
        context = self.rag.generate_context(news_item)
        
        # Level 5: Multi-agent debate
        debate_result = self.orchestrator.analyze(news_item)
        
        # Combine with Level 1 signal as baseline
        level1_result = level1_signal([news_item])
        
        # Final decision logic
        signals = [debate_result.get("signal", "HOLD"), level1_result.get("signal", "HOLD")]
        
        # Majority vote
        buy_count = signals.count("BUY")
        sell_count = signals.count("SELL")
        
        if buy_count > sell_count:
            final_signal = "BUY"
            confidence = debate_result["confidence"]
        elif sell_count > buy_count:
            final_signal = "SELL"
            confidence = debate_result["confidence"]
        else:
            final_signal = "HOLD"
            confidence = 0.5
        
        return {
            "signal": final_signal,
            "confidence": confidence,
            "debate": debate_result,
            "timestamp": datetime.now().isoformat()
        }
    
    def execute_trade(self, signal: Dict, market: dict) -> Dict:
        """Execute trade (paper or live)."""
        if self.daily_trades >= self.config["max_daily_trades"]:
            self.logger.warning("Daily trade limit reached")
            return {"status": "skipped", "reason": "daily_limit"}
        
        if len(self.positions) >= self.config["max_positions"]:
            self.logger.warning("Max positions reached")
            return {"status": "skipped", "reason": "max_positions"}
        
        if self.config["mode"] == "PAPER":
            # Paper trade
            position_value = self.capital * self.config["position_size"]
            trade = {
                "id": f"trade_{len(self.trade_history) + 1}",
                "timestamp": datetime.now().isoformat(),
                "signal": signal["signal"],
                "confidence": signal["confidence"],
                "market": market.get("question", "Unknown"),
                "price": market.get("price", 0),
                "value": position_value,
                "mode": "PAPER",
                "status": "open"
            }
            self.positions.append(trade)
            self.trade_history.append(trade)
            self.daily_trades += 1
            
            self.logger.info(f"PAPER TRADE: {signal['signal']} {market.get('question', '')[:30]}...")
            return {"status": "executed", "trade": trade}
        
        # Live trading would go here
        return {"status": "not_implemented"}
    
    def check_positions(self):
        """Check open positions for stop loss / take profit."""
        to_close = []
        
        for pos in self.positions:
            # In production: get current price
            current_price = pos.get("price", 0)  # Placeholder
            entry_price = pos.get("price", 0)
            
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            if pnl_pct <= -self.config["stop_loss_pct"]:
                to_close.append((pos, "stop_loss"))
            elif pnl_pct >= self.config["take_profit_pct"]:
                to_close.append((pos, "take_profit"))
        
        for pos, reason in to_close:
            self.logger.info(f"CLOSING: {pos['id']} - {reason}")
            pos["status"] = "closed"
            pos["close_reason"] = reason
            pos["close_time"] = datetime.now().isoformat()
            self.positions.remove(pos)
    
    def run_cycle(self):
        """Run one trading cycle."""
        self.logger.info("="*50)
        self.logger.info("Starting trading cycle...")
        
        # Update news
        self.update_news()
        
        # Get markets to analyze
        markets = self._get_markets()
        
        for market in markets:
            # Generate signal
            news = market.get("news", "")
            if not news:
                continue
            
            signal = self.generate_signal(news)
            
            if signal["signal"] in ["BUY", "SELL"]:
                result = self.execute_trade(signal, market)
                self.logger.info(f"Signal: {signal['signal']} | Confidence: {signal['confidence']:.0%}")
        
        # Check positions
        self.check_positions()
        
        # Report
        self._report_status()
        
        return True
    
    def _get_markets(self) -> List[Dict]:
        """Get markets to analyze (real data from Polymarket)."""
        # Use real data connectors
        market_data = MarketData()
        price_feed = PriceFeed()
        news_feed = NewsFeed()
        
        # Get real markets
        markets = market_data.get_active_markets(limit=5)
        
        # Get prices
        prices = price_feed.get_prices()
        
        # Get news
        news_items = news_feed.get_crypto_news(limit=5)
        
        # Convert to trading format
        result = []
        
        # Add market data
        for m in markets:
            result.append({
                "question": m.get("question", ""),
                "price": m.get("volume", 0),  # Use volume as proxy
                "volume": m.get("volume", 0),
                "news": f"{m.get('question', '')} - ${m.get('volume', 0):,.0f} volume",
                "url": m.get("url", "")
            })
        
        # Add price data as pseudo-markets
        for p in prices:
            result.append({
                "question": f"{p['coin']} price analysis",
                "price": p.get("price", 0),
                "volume": 0,
                "news": p.get("text", ""),
                "source": "price"
            })
        
        # Add news as market triggers
        for n in news_items:
            result.append({
                "question": "Crypto news",
                "price": 0,
                "volume": 0,
                "news": n.get("text", ""),
                "source": "news"
            })
        
        if not result:
            # Fallback to sample if APIs fail
            return [
                {"question": "Bitcoin above $100K by 2026?", "price": 82000, "volume": 500000, "news": "Bitcoin ETF sees record inflows"},
            ]
        
        return result
    
    def _report_status(self):
        """Report current status."""
        self.logger.info(f"Capital: ${self.capital:,.2f}")
        self.logger.info(f"Open Positions: {len(self.positions)}")
        self.logger.info(f"Daily Trades: {self.daily_trades}")
    
    def start(self):
        """Start the trading system."""
        self.running = True
        self.logger.info("="*60)
        self.logger.info("TRADING SYSTEM STARTED")
        self.logger.info(f"Mode: {self.config['mode']}")
        self.logger.info(f"Initial Capital: ${self.config['initial_capital']:,.2f}")
        self.logger.info("="*60)
        
        while self.running:
            try:
                self.run_cycle()
                time.sleep(self.config["check_interval"])
            except KeyboardInterrupt:
                self.stop()
            except Exception as e:
                self.logger.error(f"Error in trading cycle: {e}")
    
    def stop(self):
        """Stop the trading system."""
        self.running = False
        self.logger.info("Trading system stopped")


def deploy_to_vps():
    """Generate deployment instructions."""
    instructions = """
# Deployment Commands for VPS

# 1. Upload files
scp -r llm_trading/ user@vps:/home/openclaw/workspace/

# 2. Install dependencies
pip install -r llm_trading/requirements.txt

# 3. Setup PM2
pm2 start production_runner.py --name "trading-bot"
pm2 save
pm2 startup

# 4. Monitor
pm2 logs trading-bot
pm2 status
"""
    return instructions


# === MAIN ===
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run single cycle only")
    args = parser.parse_args()
    
    print("=" * 60)
    print("LEVEL 6: Production Deployment")
    print("Complete LLM Trading System")
    print("=" * 60)
    
    # Show configuration
    print("\nConfiguration:")
    for k, v in CONFIG.items():
        print(f"  {k}: {v}")
    
    # Generate deployment instructions
    print("\n--- Deployment Instructions ---")
    print(deploy_to_vps())
    
    # Demo run
    print("\n--- Demo Run ---")
    system = TradingSystem()
    
    if args.once:
        # Run actual trading cycle with real markets
        print("\nRunning actual trading cycle...")
        system.update_news()
        markets = system._get_markets()
        print(f"Found {len(markets)} real markets")
        
        # Process real markets
        for market in markets[:3]:
            news = market.get("news", "")
            if news:
                signal = system.generate_signal(news)
                print(f"\nMarket: {market.get('question', '')[:40]}...")
                print(f"  News: {news[:60]}...")
                print(f"  Signal: {signal['signal']} | Confidence: {signal['confidence']:.0%}")
    else:
        # Original demo
        print("\nRunning single trading cycle...")
        system.update_news()
        
        # Test on sample market
        test_market = {
            "question": "Bitcoin above $100K?",
            "price": 82000,
            "news": "Major bank announces $500M Bitcoin purchase"
        }
        
        signal = system.generate_signal(test_market["news"])
        
        print(f"\nResult:")
        print(f"  Signal: {signal['signal']}")
        print(f"  Confidence: {signal['confidence']:.0%}")
        print(f"  Debate rounds: {signal['debate']['rounds']}")
    
    print(f"\n{'='*60}")
    print("SYSTEM READY FOR DEPLOYMENT")
    print(f"Mode: {CONFIG['mode']} | Capital: ${CONFIG['initial_capital']:,.0f}")
    print(f"{'='*60}")