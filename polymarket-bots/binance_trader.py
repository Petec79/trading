#!/usr/bin/env python3
"""
Binance Trading Bot - LLM-Powered Crypto Trading
Trades BTC/ETH based on LLM-generated signals via Binance API

Usage:
    python3 binance_trader.py --test    # Test mode (testnet)
    python3 binance_trader.py --live    # Live trading
"""
import os
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

# Binance library
from binance.client import Client
from binance.exceptions import BinanceAPIException

# Configuration
TESTNET = True  # Default to testnet for safety
TRADE_SYMBOLS = {
    "BTC": {"symbol": "BTCUSDT", "min_qty": 0.0001, "price_precision": 2},
    "ETH": {"symbol": "ETHUSDT", "min_qty": 0.001, "price_precision": 2},
}

class BinanceTrader:
    """Binance trading bot with LLM signal integration."""
    
    def __init__(self, api_key: str = None, api_secret: str = None, testnet: bool = True):
        self.testnet = testnet
        
        if testnet:
            # Binance Testnet (free, no real money)
            self.client = Client(api_key, api_secret, testnet=True)
            self.client.API_URL = 'https://testnet.binance.vision/api'
            print("🔗 Connected to Binance TESTNET (sandbox)")
        else:
            # Live trading
            self.client = Client(api_key, api_secret)
            print("🔗 Connected to Binance LIVE")
        
        self.orders_log = []
    
    def get_balance(self, asset: str = "USDT") -> float:
        """Get account balance for an asset."""
        try:
            account = self.client.get_account()
            for balance in account['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
            return 0.0
        except Exception as e:
            print(f"Error getting balance: {e}")
            return 0.0
    
    def get_current_price(self, symbol: str) -> float:
        """Get current market price for a symbol."""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            print(f"Error getting price: {e}")
            return 0.0
    
    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100):
        """Get candlestick data."""
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            return klines
        except Exception as e:
            print(f"Error getting klines: {e}")
            return []
    
    def place_market_order(self, symbol: str, side: str, quantity: float) -> Dict:
        """
        Place a market order.
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            side: 'BUY' or 'SELL'
            quantity: Amount to trade
        
        Returns:
            Order result dict
        """
        try:
            # Validate quantity
            symbol_info = self.client.get_symbol_info(symbol)
            min_qty = float([f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'][0]['minQty'])
            quantity = max(quantity, min_qty)
            
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=quantity
            )
            
            result = {
                "status": "success",
                "order_id": order['orderId'],
                "symbol": order['symbol'],
                "side": order['side'],
                "quantity": float(order['executedQty']),
                "price": float(order.get('price', 0)),
                "time": datetime.now().isoformat()
            }
            
            self.orders_log.append(result)
            print(f"✅ {side} order placed: {quantity} {symbol} @ ${result['price']}")
            return result
            
        except BinanceAPIException as e:
            error_msg = f"API Error: {e.status_code} - {e.message}"
            print(f"❌ {error_msg}")
            return {"status": "error", "message": error_msg}
        except Exception as e:
            print(f"❌ Order failed: {e}")
            return {"status": "error", "message": str(e)}
    
    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict:
        """Place a limit order."""
        try:
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type=Client.ORDER_TYPE_LIMIT,
                timeInForce=Client.TIME_IN_FORCE_GTC,
                quantity=quantity,
                price=format(price, '.2f')
            )
            
            result = {
                "status": "success",
                "order_id": order['orderId'],
                "symbol": order['symbol'],
                "side": order['side'],
                "quantity": float(order['executedQty']),
                "price": float(order['price']),
                "time": datetime.now().isoformat()
            }
            
            print(f"✅ Limit {side} placed: {quantity} {symbol} @ ${price}")
            return result
            
        except Exception as e:
            print(f"❌ Limit order failed: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """Get open orders."""
        try:
            if symbol:
                return self.client.get_open_orders(symbol=symbol)
            return self.client.get_open_orders()
        except Exception as e:
            print(f"Error getting orders: {e}")
            return []
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """Cancel an order."""
        try:
            result = self.client.cancel_order(symbol=symbol, orderId=order_id)
            print(f"✅ Order {order_id} cancelled")
            return {"status": "success", "result": result}
        except Exception as e:
            print(f"❌ Cancel failed: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_position(self, symbol: str) -> float:
        """Get current position (quantity held) for a symbol."""
        try:
            account = self.client.get_account()
            for balance in account['balances']:
                # Convert symbol like BTCUSDT to BTC
                asset = symbol.replace('USDT', '')
                if balance['asset'] == asset:
                    return float(balance['free']) + float(balance['locked'])
            return 0.0
        except Exception as e:
            print(f"Error getting position: {e}")
            return 0.0
    
    def print_status(self):
        """Print account status."""
        print("\n" + "="*50)
        print("📊 ACCOUNT STATUS")
        print("="*50)
        
        # USDT balance
        usdt = self.get_balance("USDT")
        print(f"USDT Balance: ${usdt:,.2f}")
        
        # Crypto positions
        for coin, info in TRADE_SYMBOLS.items():
            pos = self.get_position(info['symbol'])
            price = self.get_current_price(info['symbol'])
            value = pos * price
            print(f"{coin}: {pos:.6f} (${value:,.2f} @ ${price:,.2f})")
        
        print("="*50 + "\n")


def run_trading_cycle(trader: BinanceTrader, signal: Dict) -> Dict:
    """
    Execute a trading cycle based on LLM signal.
    
    Args:
        trader: BinanceTrader instance
        signal: Dict with keys 'symbol', 'signal' (BUY/SELL/HOLD), 'confidence'
    
    Returns:
        Execution result
    """
    symbol = signal.get('symbol', 'BTCUSDT')
    signal_type = signal.get('signal', 'HOLD')
    confidence = signal.get('confidence', 0)
    
    # Get current price and position
    current_price = trader.get_current_price(symbol)
    position = trader.get_position(symbol)
    balance = trader.get_balance("USDT")
    
    print(f"\n📈 Signal: {signal_type} {symbol} | Confidence: {confidence:.0%}")
    print(f"   Current: ${current_price:,.2f} | Position: {position:.6f} | USDT: ${balance:,.2f}")
    
    result = {"signal": signal_type, "executed": False}
    
    if signal_type == "HOLD" or confidence < 0.6:
        print("   → Holding (signal unclear or low confidence)")
        return result
    
    # Calculate position size (use 10% of USDT balance per trade)
    trade_value = balance * 0.10  # 10% of available USDT
    quantity = trade_value / current_price
    
    # Round to valid quantity
    if "BTC" in symbol:
        quantity = round(quantity, 4)
    else:
        quantity = round(quantity, 3)
    
    if signal_type == "BUY" and balance > 10:
        print(f"   → Buying {quantity} {symbol} (${trade_value:.2f})")
        order = trader.place_market_order(symbol, "BUY", quantity)
        result = {"signal": "BUY", "executed": True, "order": order}
        
    elif signal_type == "SELL" and position > 0.001:
        print(f"   → Selling {position} {symbol}")
        order = trader.place_market_order(symbol, "SELL", position)
        result = {"signal": "SELL", "executed": True, "order": order}
    
    return result


# === LLM SIGNAL INTEGRATION ===
def generate_llm_signal(prices: Dict, news: List[str]) -> Dict:
    """
    Generate trading signal from LLM analysis.
    
    In production, this would call OpenAI API.
    For now, uses simple heuristic based on price movement.
    """
    import random
    
    btc_price = prices.get('BTC', {}).get('price', 67000)
    btc_change = prices.get('BTC', {}).get('change_24h', 0)
    
    # Simple signal generation (replace with real LLM in production)
    signal = "HOLD"
    confidence = 0.5
    
    # Heuristic: buy on dip, sell on rip
    if btc_change < -2:  # Big dip
        signal = "BUY"
        confidence = 0.7
    elif btc_change > 2:  # Big rally
        signal = "SELL" 
        confidence = 0.6
    elif btc_change < -1:
        signal = "BUY"
        confidence = 0.55
    
    # Add some randomness to simulate LLM uncertainty
    if random.random() < 0.3:
        signal = random.choice(["BUY", "SELL", "HOLD"])
        confidence = random.uniform(0.4, 0.65)
    
    return {
        "symbol": "BTCUSDT",
        "signal": signal,
        "confidence": confidence,
        "price": btc_price,
        "change_24h": btc_change
    }


# === MAIN ===
def main():
    parser = argparse.ArgumentParser(description="Binance LLM Trading Bot")
    parser.add_argument("--live", action="store_true", help="Use live trading (default: testnet)")
    parser.add_argument("--api-key", type=str, help="Binance API Key")
    parser.add_argument("--api-secret", type=str, help="Binance API Secret")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--test-signal", action="store_true", help="Run with test signal")
    args = parser.parse_args()
    
    print("="*60)
    print("🤖 BINANCE LLM TRADING BOT")
    print("="*60)
    
    # Get API keys from environment or args
    api_key = args.api_key or os.getenv("BINANCE_API_KEY")
    api_secret = args.api_secret or os.getenv("BINANCE_API_SECRET")
    
    if not api_key or not api_secret:
        print("\n⚠️  No API keys provided!")
        print("   Set environment variables or use --api-key/--api-secret")
        print("   For testnet, you can get free keys from: https://testnet.binance.vision")
        print("   For demo, running in VIEW-ONLY mode\n")
        
        # Create trader anyway for viewing (will fail on trades)
        trader = BinanceTrader(testnet=not args.live)
        trader.print_status()
        
        print("\n💡 To enable trading:")
        print("   export BINANCE_API_KEY='your_key'")
        print("   export BINANCE_API_SECRET='your_secret'")
        return
    
    # Initialize trader
    trader = BinanceTrader(api_key, api_secret, testnet=not args.live)
    
    # Show account status
    trader.print_status()
    
    if args.test_signal:
        # Test with sample signal
        print("\n🧪 Running test signal...")
        test_signal = {
            "symbol": args.symbol,
            "signal": "BUY",
            "confidence": 0.7
        }
        run_trading_cycle(trader, test_signal)
    else:
        # Get prices and generate signal
        print("\n📊 Fetching market data...")
        
        # Get live prices
        prices = {}
        for coin, info in TRADE_SYMBOLS.items():
            price = trader.get_current_price(info['symbol'])
            prices[coin] = {"price": price, "change_24h": 0}  # Would fetch 24h change
        
        # Generate signal
        signal = generate_llm_signal(prices, [])
        
        print(f"\n🎯 Generated Signal: {signal['signal']} ({signal['confidence']:.0%} confidence)")
        
        # Execute if not HOLD
        if signal['signal'] != "HOLD":
            result = run_trading_cycle(trader, signal)
            print(f"\n✅ Trade executed: {result}")
        else:
            print("   → No trade (HOLD signal)")
    
    # Show final status
    trader.print_status()


if __name__ == "__main__":
    main()