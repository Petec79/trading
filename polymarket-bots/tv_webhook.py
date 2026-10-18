"""
TradingView → Polymarket Webhook Server
Receives TradingView alerts and executes trades on Polymarket

Run: python3 tv_webhook.py
"""
import os
import json
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configuration
POLLMARKET_API = "https://gamma-api.polymarket.com"
PAPER_MODE = True  # Set to False for live trading

# Market mapping: TradingView symbol -> Polymarket keyword search
MARKET_MAPPING = {
    # Crypto
    "BTCUSD": {"keyword": "bitcoin", "category": "crypto"},
    "ETHUSD": {"keyword": "ethereum", "category": "crypto"},
    "SOLUSD": {"keyword": "solana", "category": "crypto"},
    
    # Tech stocks
    "NVDA": {"keyword": "Nvidia", "category": "stocks"},
    "AAPL": {"keyword": "Apple", "category": "stocks"},
    "TSLA": {"keyword": "Tesla", "category": "stocks"},
    "MSFT": {"keyword": "Microsoft", "category": "stocks"},
    "GOOGL": {"keyword": "Google", "category": "stocks"},
    
    # Indices
    "SPY": {"keyword": "S&P 500", "category": "index"},
    "QQQ": {"keyword": "NASDAQ", "category": "index"},
    
    # Events
    "GTAVI": {"keyword": "GTA VI", "category": "event"},
    "ELECTION": {"keyword": "election", "category": "event"},
}

# Signal mapping: TradingView alert message -> action
SIGNAL_MAPPING = {
    # Buy signals
    "RSI Oversold": {"action": "BUY", "side": "YES"},
    "RSI Oversold - BUY": {"action": "BUY", "side": "YES"},
    "Golden Cross": {"action": "BUY", "side": "YES"},
    "Bullish Divergence": {"action": "BUY", "side": "YES"},
    "EMA Bullish Cross": {"action": "BUY", "side": "YES"},
    "Buy": {"action": "BUY", "side": "YES"},
    "LONG": {"action": "BUY", "side": "YES"},
    
    # Sell signals  
    "RSI Overbought": {"action": "SELL", "side": "NO"},
    "RSI Overbought - SELL": {"action": "SELL", "side": "NO"},
    "Death Cross": {"action": "SELL", "side": "NO"},
    "Bearish Divergence": {"action": "SELL", "side": "NO"},
    "EMA Bearish Cross": {"action": "SELL", "side": "NO"},
    "Sell": {"action": "SELL", "side": "NO"},
    "SHORT": {"action": "SELL", "side": "NO"},
}

# Trade log
TRADES = []

def find_market(keyword, limit=10):
    """Find Polymarket market by keyword."""
    try:
        resp = requests.get(
            f"{POLLMARKET_API}/markets",
            params={"limit": limit, "closed": "false", "search": keyword},
            timeout=10
        )
        if resp.ok:
            markets = resp.json()
            if markets:
                # Return highest volume match
                markets.sort(key=lambda x: float(x.get('volume', 0)), reverse=True)
                return markets[0]
    except Exception as e:
        print(f"[find_market] Error: {e}")
    return None

def parse_tradingview_alert(data):
    """Parse TradingView alert and extract signal."""
    # Try multiple formats
    
    # Format 1: {{strategy.order.action}} {{ticker}}
    message = data.get('message', '')
    if not message:
        message = data.get('text', '')
    if not message:
        message = data.get('alert', '')
    
    # Extract ticker
    ticker = data.get('ticker', '')
    if not ticker and message:
        # Try to extract from message
        parts = message.split()
        for part in parts:
            if part.upper() in MARKET_MAPPING:
                ticker = part.upper()
                break
    
    # Extract action
    action = None
    side = None
    
    for signal_name, signal_info in SIGNAL_MAPPING.items():
        if signal_name.lower() in message.lower():
            action = signal_info['action']
            side = signal_info['side']
            break
    
    return {
        'ticker': ticker,
        'action': action,
        'side': side,
        'message': message,
        'price': data.get('price', 0),
        'time': data.get('time', datetime.now().isoformat())
    }

def execute_trade(market, side, size=10):
    """Execute trade (paper or live)."""
    if PAPER_MODE:
        trade = {
            'timestamp': datetime.now().isoformat(),
            'market': market.get('question', 'Unknown')[:50],
            'side': side,
            'size': size,
            'mode': 'PAPER',
            'status': 'simulated'
        }
        TRADES.append(trade)
        return trade
    else:
        # Live trading would go here
        pass

@app.route('/webhook/tradingview', methods=['POST'])
def tradingview_webhook():
    """TradingView webhook endpoint."""
    try:
        data = request.get_json() or request.form.to_dict()
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Received TradingView alert:")
        print(f"  Data: {json.dumps(data)[:200]}")
        
        # Parse the alert
        signal = parse_tradingview_alert(data)
        
        if not signal['ticker']:
            return jsonify({'error': 'No ticker found in alert'}), 400
        
        if not signal['action']:
            return jsonify({'error': 'Unknown signal type'}), 400
        
        # Find matching market
        market_info = MARKET_MAPPING.get(signal['ticker'], {})
        keyword = market_info.get('keyword', signal['ticker'])
        
        market = find_market(keyword)
        
        if not market:
            return jsonify({
                'error': f'No market found for {signal["ticker"]}',
                'keyword': keyword
            }), 404
        
        # Execute trade
        trade = execute_trade(market, signal['side'], size=10)
        
        return jsonify({
            'status': 'success',
            'signal': signal,
            'market': market.get('question', '')[:50],
            'volume': market.get('volume'),
            'trade': trade
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'trades': len(TRADES),
        'paper_mode': PAPER_MODE
    })

@app.route('/trades', methods=['GET'])
def get_trades():
    return jsonify({'trades': TRADES})

@app.route('/markets', methods=['GET'])
def list_markets():
    """List available market mappings."""
    return jsonify({'mappings': MARKET_MAPPING})

if __name__ == '__main__':
    print("=" * 60)
    print("TradingView → Polymarket Webhook Server")
    print("=" * 60)
    print(f"Mode: {'PAPER' if PAPER_MODE else 'LIVE'}")
    print(f"Endpoint: http://localhost:5000/webhook/tradingview")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)