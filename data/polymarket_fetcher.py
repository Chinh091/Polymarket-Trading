"""
data/polymarket_fetcher.py
Fetches markets and orderbook data from Polymarket's public APIs.
No authentication required for read-only access.
"""
import requests
import time
import asyncio
import websockets
import json
import logging
from datetime import datetime
from core.database import save_markets, save_price_snapshot
from core.logger import setup_logger

logger = setup_logger("PolymarketFetcher")

GAMMA_API   = "https://gamma-api.polymarket.com"
CLOB_API    = "https://clob.polymarket.com"
CLOB_WS     = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketFetcher:
    """
    Fetches live market data from Polymarket.
    Gamma API  → market discovery (questions, volumes, categories)
    CLOB API   → orderbook depth, mid prices, spreads
    CLOB WS    → real-time price stream (optional)
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PolymarketBot/1.0"})

    # ------------------------------------------------------------------
    # GAMMA API - Market Discovery
    # ------------------------------------------------------------------

    def fetch_active_markets(self, limit: int = 100) -> list:
        """Get all currently active markets sorted by volume."""
        try:
            url = f"{GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "volume",
                "ascending": "false"
            }
            r = self.session.get(url, params=params, timeout=15)
            r.raise_for_status()
            markets = r.json()
            logger.info(f"Fetched {len(markets)} active markets from Gamma API")
            save_markets(markets)
            return markets
        except Exception as e:
            logger.error(f"Gamma API fetch failed: {e}")
            return []

    def fetch_crypto_markets(self) -> list:
        """Get 15-minute BTC/ETH/SOL up/down markets specifically."""
        try:
            markets = self.fetch_active_markets(limit=500)
            crypto_keywords = [
                "bitcoin", "btc", "ethereum", "eth",
                "solana", "sol", "15-minute", "15 minute",
                "up or down", "higher or lower"
            ]
            crypto = []
            for m in markets:
                q = m.get("question", "").lower()
                if any(kw in q for kw in crypto_keywords):
                    crypto.append(m)
            logger.info(f"Found {len(crypto)} crypto markets")
            return crypto
        except Exception as e:
            logger.error(f"Crypto market filter failed: {e}")
            return []

    def fetch_market_by_id(self, condition_id: str) -> dict:
        """Get full details for one market."""
        try:
            url = f"{GAMMA_API}/markets/{condition_id}"
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Market fetch failed for {condition_id}: {e}")
            return {}

    # ------------------------------------------------------------------
    # CLOB API - Orderbook Data
    # ------------------------------------------------------------------

    def fetch_orderbook(self, token_id: str) -> dict:
        """Get full orderbook for a market token (YES or NO side)."""
        try:
            url = f"{CLOB_API}/book"
            params = {"token_id": token_id}
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Orderbook fetch failed for {token_id}: {e}")
            return {}

    def fetch_mid_price(self, token_id: str) -> float:
        """Get current mid price for a token."""
        try:
            url = f"{CLOB_API}/midpoint"
            params = {"token_id": token_id}
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            return float(data.get("mid", 0))
        except Exception as e:
            logger.error(f"Mid price fetch failed for {token_id}: {e}")
            return 0.0

    def fetch_spread(self, token_id: str) -> dict:
        """Get best bid/ask spread for a token."""
        try:
            book = self.fetch_orderbook(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid,
                "mid": (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            }
        except Exception as e:
            logger.error(f"Spread fetch failed: {e}")
            return {"best_bid": 0, "best_ask": 0, "spread": 0, "mid": 0}

    def fetch_and_store_prices(self, markets: list):
        """Fetch orderbook data for a list of markets and save to DB."""
        updated = 0
        for market in markets:
            token_ids = market.get("clobTokenIds", [])
            condition_id = market.get("condition_id", market.get("id", ""))
            if not token_ids or not condition_id:
                continue
            # YES side
            if len(token_ids) > 0:
                spread = self.fetch_spread(token_ids[0])
                save_price_snapshot(
                    condition_id, "YES",
                    spread["best_bid"], spread["best_ask"]
                )
            # NO side
            if len(token_ids) > 1:
                spread = self.fetch_spread(token_ids[1])
                save_price_snapshot(
                    condition_id, "NO",
                    spread["best_bid"], spread["best_ask"]
                )
            updated += 1
            time.sleep(0.1)  # Be respectful to the API
        logger.info(f"Updated prices for {updated} markets")

    # ------------------------------------------------------------------
    # CLOB WebSocket - Real-time Stream
    # ------------------------------------------------------------------

    async def stream_prices(self, token_ids: list, callback):
        """
        Subscribe to real-time price updates for a list of token IDs.
        callback(token_id, price_data) is called on every update.
        """
        if not token_ids:
            logger.warning("No token IDs provided for WebSocket stream")
            return

        subscribe_msg = {
            "auth": {},
            "markets": token_ids,
            "type": "market"
        }

        while True:
            try:
                logger.info(f"Connecting to CLOB WebSocket for {len(token_ids)} tokens")
                async with websockets.connect(CLOB_WS, ping_interval=30) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("WebSocket connected - streaming live prices")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            events = data if isinstance(data, list) else [data]
                            for event in events:
                                token_id = event.get("asset_id", "")
                                if token_id and callback:
                                    await callback(token_id, event)
                        except json.JSONDecodeError:
                            pass
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket disconnected - reconnecting in 5s")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"WebSocket error: {e} - reconnecting in 10s")
                await asyncio.sleep(10)

    def get_implied_probability(self, token_id: str) -> float:
        """
        Returns the implied probability (0–1) for a YES token.
        Mid price IS the implied probability on Polymarket.
        e.g. mid=0.72 means market thinks 72% chance of YES
        """
        return self.fetch_mid_price(token_id)
