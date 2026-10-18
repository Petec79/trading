#!/usr/bin/env python3
"""
Swing Signal Chart Server
Serves the HTML dashboard + proxies Binance API calls to bypass CORS.
"""
import http.server
import socketserver
import urllib.request
import json
import os
from urllib.parse import parse_qs, urlparse

PORT = 3456
DIRECTORY = "/home/openclaw/workspace/strategies"

class SwingHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self.handle_proxy()
        else:
            super().do_GET()

    def handle_proxy(self):
        """Proxy Binance API calls to bypass CORS."""
        # Parse: /api/klines?symbol=BTCUSDT&interval=4h&limit=500
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        symbol = params.get("symbol", ["BTCUSDT"])[0]
        interval = params.get("interval", ["4h"])[0]
        limit = params.get("limit", ["500"])[0]

        binance_url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}&limit={limit}"
        )

        try:
            req = urllib.request.Request(
                binance_url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def end_headers(self):
        # Add CORS for static files
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

if __name__ == "__main__":
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", PORT), SwingHandler) as httpd:
        print(f"Swing Signal Chart running at http://0.0.0.0:{PORT}")
        httpd.serve_forever()
