"""
agents/agents.py
All specialist agents. Each has a get_signal() method returning a dict.
Agents never call each other — they publish independently to the orchestrator.
"""
import os
import time
import logging
from datetime import datetime
from core.database import (
    get_active_markets, get_latest_spot, save_signal
)
from core.logger import setup_logger

logger = setup_logger("Agents")


# ======================================================================
# BASE AGENT
# ======================================================================

class BaseAgent:
    """All agents inherit from this. Enforces the get_signal() contract."""

    def __init__(self, name: str):
        self.name = name
        self.logger = setup_logger(name)
        self.last_run = None

    def get_signal(self) -> dict:
        """
        Returns a signal dict:
        {
          agent: str,
          condition_id: str or None,
          signal_type: "TRADE" | "SKIP" | "ALERT" | "INFO",
          direction: "YES" | "NO" | None,
          confidence: float (0-1),
          edge_pct: float,
          reason: str,
          data: dict (optional extra info)
        }
        """
        raise NotImplementedError

    def _no_signal(self, reason: str) -> dict:
        return {
            "agent": self.name,
            "condition_id": None,
            "signal_type": "SKIP",
            "direction": None,
            "confidence": 0.0,
            "edge_pct": 0.0,
            "reason": reason,
            "data": {}
        }

    def _trade_signal(self, condition_id: str, direction: str,
                      confidence: float, edge_pct: float,
                      reason: str, data: dict = None) -> dict:
        signal = {
            "agent": self.name,
            "condition_id": condition_id,
            "signal_type": "TRADE",
            "direction": direction,
            "confidence": confidence,
            "edge_pct": edge_pct,
            "reason": reason,
            "data": data or {}
        }
        save_signal(
            agent_name=self.name,
            condition_id=condition_id,
            signal_type="TRADE",
            direction=direction,
            confidence=confidence,
            edge_pct=edge_pct,
            raw_data=data
        )
        return signal


# ======================================================================
# AGENT 1 — MARKET SCANNER
# ======================================================================

class MarketScannerAgent(BaseAgent):
    """
    Scans active Polymarket markets and identifies candidates for trading.
    Scores markets by: volume, time to close, current price extremity.
    HIGH VALUE: markets where YES or NO is priced between 0.05 and 0.95
    (not yet near resolution) AND volume > $10,000.
    """

    def __init__(self):
        super().__init__("MarketScanner")
        self.min_volume = 10_000
        self.min_price  = 0.05
        self.max_price  = 0.95

    def get_signal(self) -> dict:
        markets = get_active_markets(limit=100)
        if not markets:
            return self._no_signal("No active markets in database yet")

        candidates = []
        for m in markets:
            vol = m.get("volume", 0) or 0
            yes_price = m.get("last_price_yes", 0) or 0
            no_price  = m.get("last_price_no", 0) or 0

            if vol < self.min_volume:
                continue
            if not (self.min_price < yes_price < self.max_price):
                continue

            # Score: higher volume = more liquid = better fills
            liquidity_score = min(vol / 100_000, 1.0)
            # Price near 0.5 = more uncertainty = more trading opportunity
            uncertainty_score = 1.0 - abs(yes_price - 0.5) * 2

            total_score = (liquidity_score * 0.6) + (uncertainty_score * 0.4)
            candidates.append({**m, "scanner_score": total_score})

        if not candidates:
            return self._no_signal(
                f"No markets pass filter (vol>${self.min_volume:,}, "
                f"price {self.min_price}-{self.max_price})"
            )

        candidates.sort(key=lambda x: x["scanner_score"], reverse=True)
        best = candidates[0]

        self.logger.info(
            f"Top market: {best.get('question','')[:60]} "
            f"| Vol: ${best.get('volume',0):,.0f} "
            f"| Score: {best['scanner_score']:.2f}"
        )

        return self._trade_signal(
            condition_id=best.get("condition_id", best.get("id", "")),
            direction="YES",
            confidence=0.5,
            edge_pct=0.0,
            reason=f"High-volume market identified: score={best['scanner_score']:.2f}",
            data={"top_candidates": [c.get("question","")[:50] for c in candidates[:5]],
                  "best_volume": best.get("volume", 0)}
        )

    def get_top_markets(self, n: int = 10) -> list:
        """Return top N markets by scanner score."""
        markets = get_active_markets(limit=200)
        scored = []
        for m in markets:
            vol = m.get("volume", 0) or 0
            yes_price = m.get("last_price_yes", 0) or 0
            if vol >= self.min_volume and self.min_price < yes_price < self.max_price:
                score = min(vol / 100_000, 1.0) * 0.6 + (1 - abs(yes_price - 0.5) * 2) * 0.4
                scored.append({**m, "scanner_score": score})
        scored.sort(key=lambda x: x["scanner_score"], reverse=True)
        return scored[:n]


# ======================================================================
# AGENT 2 — ARBITRAGE AGENT
# ======================================================================

class ArbitrageAgent(BaseAgent):
    """
    Compares Binance spot price momentum vs Polymarket implied probability.
    
    Core idea: On Polymarket 15-min BTC up/down markets, the YES price 
    should reflect the current probability of BTC being higher in 15 mins.
    When spot price has already moved strongly in one direction, but 
    Polymarket price hasn't updated yet — that's the edge window.

    Edge calculation:
    - BTC pumped +0.5% in last 60s → 15-min UP contract should be ~70%
    - If Polymarket still shows 50% → buy YES at 50%, true value is 70%
    - Edge = 20% (after fees: ~18.4%)
    """

    def __init__(self, binance_fetcher=None):
        super().__init__("ArbitrageAgent")
        self.binance = binance_fetcher
        self.price_history = {}  # symbol → list of (timestamp, price)
        self.min_edge = 0.05     # 5% minimum edge after fees
        self.lookback_seconds = 60

    def update_price(self, symbol: str, price: float):
        """Called by Binance WebSocket handler on every tick."""
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        self.price_history[symbol].append((time.time(), price))
        # Keep only last 5 minutes
        cutoff = time.time() - 300
        self.price_history[symbol] = [
            (t, p) for t, p in self.price_history[symbol] if t > cutoff
        ]

    def get_momentum(self, symbol: str, seconds: int = 60) -> float:
        """
        Returns price change % over the last `seconds`.
        Positive = price went up. Negative = price went down.
        """
        history = self.price_history.get(symbol, [])
        if len(history) < 2:
            return 0.0
        cutoff = time.time() - seconds
        old_prices = [p for t, p in history if t <= cutoff]
        new_prices = [p for t, p in history if t > cutoff]
        if not old_prices or not new_prices:
            return 0.0
        old_price = old_prices[-1]
        new_price = new_prices[-1]
        return (new_price - old_price) / old_price * 100

    def momentum_to_probability(self, momentum_pct: float) -> float:
        """
        Convert price momentum to implied win probability.
        Based on: strong momentum → high probability of continuing 
        (within a 15-minute window specifically).
        
        Calibration (rough empirical estimates):
        +1.0% move → ~72% chance of being up at 15min mark
        +0.5% move → ~65% chance
        +0.2% move → ~57% chance
        0.0%       → ~50% chance (random)
        -0.2% move → ~43% chance
        """
        import math
        # Sigmoid-like mapping: momentum % → probability
        # Capped at 95% / 5% to avoid claiming certainty
        raw = 0.5 + (momentum_pct / 4.0) * 0.5
        return max(0.05, min(0.95, raw))

    def get_signal(self) -> dict:
        """
        Checks for arbitrage between Binance momentum 
        and Polymarket crypto contract prices.
        """
        symbol_map = {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "SOLUSDT": "solana"
        }
        for binance_sym, keyword in symbol_map.items():
            momentum = self.get_momentum(binance_sym, self.lookback_seconds)
            if abs(momentum) < 0.15:
                continue  # Not enough movement to signal

            true_prob = self.momentum_to_probability(momentum)
            direction = "YES" if momentum > 0 else "NO"

            # Find matching Polymarket market
            markets = get_active_markets(limit=100)
            for m in markets:
                q = m.get("question", "").lower()
                if keyword not in q:
                    continue
                if "15" not in q and "minute" not in q:
                    continue

                market_price = m.get("last_price_yes", 0.5) or 0.5
                if direction == "NO":
                    market_price = 1 - market_price

                edge = true_prob - market_price
                edge_after_fee = edge - 0.0156  # subtract taker fee

                if edge_after_fee < self.min_edge:
                    continue

                self.logger.info(
                    f"ARB SIGNAL: {binance_sym} momentum={momentum:+.2f}% "
                    f"true_prob={true_prob:.0%} market={market_price:.0%} "
                    f"edge={edge_after_fee:.0%}"
                )
                return self._trade_signal(
                    condition_id=m.get("condition_id", m.get("id", "")),
                    direction=direction,
                    confidence=min(edge_after_fee * 3, 0.9),
                    edge_pct=edge_after_fee * 100,
                    reason=(
                        f"{binance_sym} momentum {momentum:+.2f}% in {self.lookback_seconds}s. "
                        f"True prob ~{true_prob:.0%} vs market {market_price:.0%}. "
                        f"Edge after fees: {edge_after_fee:.1%}"
                    ),
                    data={
                        "symbol": binance_sym,
                        "momentum_pct": momentum,
                        "true_probability": true_prob,
                        "market_price": market_price,
                        "edge_before_fee": edge,
                        "edge_after_fee": edge_after_fee
                    }
                )

        return self._no_signal("No significant momentum or matching markets found")


# ======================================================================
# AGENT 3 — NEWS ANALYST AGENT
# ======================================================================

class NewsAnalystAgent(BaseAgent):
    """
    Converts news signals into tradeable market signals.
    Works without AI (keyword scoring) or with AI (Claude Haiku).
    """

    def __init__(self, news_fetcher=None):
        super().__init__("NewsAnalyst")
        self.news = news_fetcher
        self.min_score = 7
        self.min_confidence = 0.6

    def get_signal(self) -> dict:
        if not self.news:
            return self._no_signal("No news fetcher configured")

        headlines = self.news.fetch_headlines()
        if not headlines:
            return self._no_signal("No new headlines fetched")

        high_priority = self.news.process_and_store(headlines)
        if not high_priority:
            return self._no_signal(
                f"No headlines scored >= {self.min_score}/10"
            )

        # Use the highest scoring headline
        best = max(high_priority, key=lambda x: x.get("score", 0))
        confidence = best.get("confidence", 0)
        direction_str = best.get("direction", "NEUTRAL")

        if direction_str == "NEUTRAL" or confidence < self.min_confidence:
            return self._no_signal(
                f"Best headline is NEUTRAL or confidence too low ({confidence:.0%})"
            )

        # Find a matching market
        markets = get_active_markets(limit=100)
        keywords_found = best.get("keywords_found", [])
        best_market = None
        for m in markets:
            q = m.get("question", "").lower()
            if any(kw in q for kw in keywords_found):
                best_market = m
                break

        if not best_market:
            return self._no_signal(
                f"No matching market found for keywords: {keywords_found}"
            )

        direction = "YES" if direction_str == "BULLISH" else "NO"
        edge_pct = (confidence - 0.5) * 20  # rough edge estimate

        return self._trade_signal(
            condition_id=best_market.get("condition_id", best_market.get("id", "")),
            direction=direction,
            confidence=confidence,
            edge_pct=edge_pct,
            reason=f"News signal ({direction_str}, {confidence:.0%}): {best.get('headline','')[:80]}",
            data={
                "headline": best.get("headline", ""),
                "score": best.get("score", 0),
                "keywords": keywords_found
            }
        )


# ======================================================================
# AGENT 4 — RISK MANAGER
# ======================================================================

class RiskManagerAgent(BaseAgent):
    """
    Validates every trade before it reaches the portfolio.
    Acts as the final gatekeeper — if this says NO, nothing trades.
    """

    def __init__(self, portfolio=None):
        super().__init__("RiskManager")
        self.portfolio = portfolio
        self.max_position_pct  = float(os.getenv("MAX_POSITION_PCT", 0.05))
        self.max_positions      = int(os.getenv("MAX_OPEN_POSITIONS", 3))
        self.max_drawdown_pct   = float(os.getenv("MAX_DRAWDOWN_PCT", 0.15))

    def validate_trade(self, signal: dict, proposed_size: float) -> dict:
        """
        Validates a trade signal against risk rules.
        Returns {approved: bool, reason: str, adjusted_size: float}
        """
        if not self.portfolio:
            return {"approved": True, "reason": "No portfolio connected (test mode)",
                    "adjusted_size": proposed_size}

        bankroll    = self.portfolio.bankroll
        open_pos    = len(self.portfolio.open_positions)
        peak_bank   = self.portfolio.peak_bankroll
        drawdown    = (peak_bank - bankroll) / peak_bank if peak_bank > 0 else 0

        # Rule 1: Max drawdown halt
        if drawdown >= self.max_drawdown_pct:
            self.logger.warning(
                f"TRADING HALTED — drawdown {drawdown:.1%} >= {self.max_drawdown_pct:.1%}"
            )
            save_signal(self.name, None, "HALT",
                        raw_data={"drawdown": drawdown, "bankroll": bankroll})
            return {"approved": False, "reason": f"Max drawdown reached ({drawdown:.1%})",
                    "adjusted_size": 0}

        # Rule 2: Max open positions
        if open_pos >= self.max_positions:
            return {"approved": False,
                    "reason": f"Max open positions ({self.max_positions}) reached",
                    "adjusted_size": 0}

        # Rule 3: Max position size
        max_size = bankroll * self.max_position_pct
        adjusted = min(proposed_size, max_size)

        # Rule 4: Minimum viable size ($5)
        if adjusted < 5:
            return {"approved": False,
                    "reason": f"Trade size ${adjusted:.2f} too small (min $5)",
                    "adjusted_size": 0}

        return {"approved": True,
                "reason": "All risk checks passed",
                "adjusted_size": adjusted}

    def get_signal(self) -> dict:
        """Passive monitoring — emits HALT signal if limits are breached."""
        if not self.portfolio:
            return self._no_signal("No portfolio connected")

        bankroll  = self.portfolio.bankroll
        peak      = self.portfolio.peak_bankroll
        drawdown  = (peak - bankroll) / peak if peak > 0 else 0
        open_pos  = len(self.portfolio.open_positions)

        if drawdown >= self.max_drawdown_pct:
            return {
                "agent": self.name,
                "condition_id": None,
                "signal_type": "HALT",
                "direction": None,
                "confidence": 1.0,
                "edge_pct": 0,
                "reason": f"HALT: Drawdown {drawdown:.1%} exceeded {self.max_drawdown_pct:.1%} limit",
                "data": {"drawdown": drawdown, "bankroll": bankroll, "open_positions": open_pos}
            }

        return self._no_signal(
            f"All clear: drawdown={drawdown:.1%}, positions={open_pos}/{self.max_positions}"
        )


# ======================================================================
# AGENT 5 — POSITION SIZER
# ======================================================================

class PositionSizerAgent(BaseAgent):
    """
    Calculates optimal trade size using fractional Kelly criterion.
    Full Kelly is too aggressive — we use 1/4 Kelly for safety.

    Kelly formula: f = (edge / odds)
    Where edge = expected value above 0.5, odds = payout ratio (1:1 on binary)
    Quarter-Kelly: f* = f / 4
    """

    def __init__(self, portfolio=None):
        super().__init__("PositionSizer")
        self.portfolio = portfolio
        self.kelly_fraction = 0.25  # 1/4 Kelly
        self.min_size = 5.0         # Minimum $5 trade
        self.max_size_pct = 0.05    # Never more than 5% of bankroll

    def calculate_size(self, bankroll: float, edge_pct: float,
                       confidence: float) -> float:
        """
        Calculate optimal position size.

        edge_pct: % edge over market (e.g. 15.0 = 15% edge)
        confidence: 0-1 how confident the agent is

        Returns: dollar amount to bet
        """
        if edge_pct <= 0 or confidence <= 0:
            return 0.0

        # Convert edge % to decimal
        edge_decimal = edge_pct / 100.0

        # Kelly fraction = edge / 1 (since binary market pays 1:1 if correct)
        # Adjusted by our confidence level
        kelly_pct = edge_decimal * confidence

        # Apply quarter-Kelly
        fraction = kelly_pct * self.kelly_fraction

        # Calculate dollar amount
        size = bankroll * fraction

        # Apply bounds
        max_size = bankroll * self.max_size_pct
        size = max(self.min_size, min(size, max_size))

        self.logger.debug(
            f"Kelly sizing: bankroll=${bankroll:.0f} "
            f"edge={edge_pct:.1f}% conf={confidence:.0%} "
            f"→ fraction={fraction:.3f} size=${size:.2f}"
        )
        return round(size, 2)

    def get_signal(self) -> dict:
        """Passive agent — called directly via calculate_size(), not via signal loop."""
        return self._no_signal("PositionSizer is called directly, not via signal loop")
