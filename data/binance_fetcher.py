"""
data/binance_fetcher.py
Streams real-time BTC/ETH/SOL prices from Binance public WebSocket.
No API key required - this is all public data.
"""
import asyncio
import websockets
import json
import requests
import logging
from datetime import datetime
from core.database import save_spot_price
from core.logger import setup_logger

logger = setup_logger("BinanceFetcher")

BINANCE_WS   = "wss://stream.binance.com:9443/ws"
BINANCE_REST = "https://api.binance.com/api/v3"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class BinanceFetcher:
    """
    Streams live spot prices from Binance.
    Used by the ArbitrageAgent to compare against Polymarket contracts.
    """

    def __init__(self):
        self.latest_prices = {}

    def fetch_price_rest(self, symbol: str) -> float:
        """Single price fetch via REST (for startup / fallback)."""
        try:
            url = f"{BINANCE_REST}/ticker/price"
            r = requests.get(url, params={"symbol": symbol}, timeout=10)
            r.raise_for_status()
            price = float(r.json()["price"])
            self.latest_prices[symbol] = price
            save_spot_price(symbol, price)
            return price
        except Exception as e:
            logger.error(f"Binance REST price fetch failed for {symbol}: {e}")
            return 0.0

    def fetch_all_prices_rest(self) -> dict:
        """Fetch all tracked symbols via REST."""
        prices = {}
        for sym in SYMBOLS:
            prices[sym] = self.fetch_price_rest(sym)
            logger.info(f"  {sym}: ${prices[sym]:,.2f}")
        return prices

    async def stream_prices(self, callback=None):
        """
        Subscribe to live trade stream for all tracked symbols.
        Updates self.latest_prices and saves to DB on every tick.
        callback(symbol, price) is called on each update.
        """
        # Build combined stream URL
        streams = "/".join([f"{s.lower()}@trade" for s in SYMBOLS])
        url = f"{BINANCE_WS}/{streams}"

        while True:
            try:
                logger.info(f"Connecting to Binance WebSocket - tracking {SYMBOLS}")
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("Binance WebSocket connected")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Combined stream wraps in {"stream": ..., "data": ...}
                            if "data" in data:
                                data = data["data"]
                            symbol = data.get("s", "")
                            price  = float(data.get("p", 0))
                            if symbol and price:
                                self.latest_prices[symbol] = price
                                save_spot_price(symbol, price)
                                if callback:
                                    await callback(symbol, price)
                        except (json.JSONDecodeError, ValueError):
                            pass
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Binance WebSocket closed - reconnecting in 5s")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Binance WebSocket error: {e} - retrying in 10s")
                await asyncio.sleep(10)

    def get_latest_price(self, symbol: str) -> float:
        """Get the most recently received price for a symbol."""
        return self.latest_prices.get(symbol, 0.0)

    def btc_price(self) -> float:
        return self.get_latest_price("BTCUSDT")

    def eth_price(self) -> float:
        return self.get_latest_price("ETHUSDT")

    def sol_price(self) -> float:
        return self.get_latest_price("SOLUSDT")
