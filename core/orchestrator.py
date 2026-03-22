"""
core/orchestrator.py
Master Orchestrator — collects signals from all agents,
requires consensus before approving trades, routes to portfolio.
"""
import asyncio
import time
import os
import logging
from datetime import datetime
from queue import Queue, Empty
from threading import Thread

from core.database import init_database, save_signal
from core.logger import setup_logger
from core.portfolio import VirtualPortfolio
from agents.agents import (
    MarketScannerAgent, ArbitrageAgent,
    NewsAnalystAgent, RiskManagerAgent, PositionSizerAgent
)
from data.polymarket_fetcher import PolymarketFetcher
from data.binance_fetcher import BinanceFetcher
from data.news_fetcher import NewsFetcher

logger = setup_logger("Orchestrator")


class MasterOrchestrator:
    """
    Coordinates all agents and executes paper trades.
    
    Flow:
    1. Agents run independently and put signals in the queue
    2. Orchestrator reads the queue and groups signals by market
    3. If 2+ agents agree on same market + direction → approve trade
    4. RiskManager validates → PositionSizer sizes → Portfolio executes
    """

    def __init__(self):
        init_database()

        self.signal_queue   = Queue()
        self.running        = False

        # Shared data fetchers
        self.polymarket     = PolymarketFetcher()
        self.binance        = BinanceFetcher()
        self.news           = NewsFetcher()

        # Portfolio (starts with $1000 virtual USDC)
        self.portfolio      = VirtualPortfolio(
            starting_bankroll=float(os.getenv("STARTING_BANKROLL", 1000.0))
        )

        # Agents
        self.scanner        = MarketScannerAgent()
        self.arbitrage      = ArbitrageAgent(binance_fetcher=self.binance)
        self.news_agent     = NewsAnalystAgent(news_fetcher=self.news)
        self.risk_manager   = RiskManagerAgent(portfolio=self.portfolio)
        self.position_sizer = PositionSizerAgent(portfolio=self.portfolio)

        # Recent signals buffer for consensus check
        self.recent_signals = []  # list of signal dicts from last 5 minutes
        self.consensus_window = 300  # 5 minutes
        self.min_consensus   = 2     # need 2 agents to agree

        # Throttles
        self.last_market_scan   = 0
        self.last_news_scan     = 0
        self.market_scan_interval = int(os.getenv("MARKET_SCAN_INTERVAL", 60))
        self.news_scan_interval   = int(os.getenv("NEWS_POLL_INTERVAL", 60))

        logger.info("Orchestrator initialised — all agents ready")

    # ------------------------------------------------------------------
    # Main Entry Point
    # ------------------------------------------------------------------

    def start(self):
        """Start the bot — runs all agents in background threads."""
        self.running = True
        logger.info("="*60)
        logger.info("  POLYMARKET PAPER TRADING BOT STARTING")
        logger.info(f"  Bankroll: ${self.portfolio.bankroll:,.2f} virtual USDC")
        logger.info("="*60)

        # Bootstrap: fetch initial market data
        logger.info("Fetching initial market data...")
        self.polymarket.fetch_active_markets(limit=200)
        self.binance.fetch_all_prices_rest()

        # Start background threads
        threads = [
            Thread(target=self._run_market_scanner,  daemon=True, name="MarketScanner"),
            Thread(target=self._run_news_agent,      daemon=True, name="NewsAgent"),
            Thread(target=self._run_risk_monitor,    daemon=True, name="RiskMonitor"),
            Thread(target=self._run_signal_processor,daemon=True, name="SignalProcessor"),
            Thread(target=self._run_binance_prices,  daemon=True, name="BinancePrices"),
        ]
        for t in threads:
            t.start()
            logger.info(f"Started thread: {t.name}")

        logger.info("All agents running. Press Ctrl+C to stop.")

        try:
            while self.running:
                time.sleep(10)
                self._print_status()
        except KeyboardInterrupt:
            logger.info("Stopping bot...")
            self.running = False
            self.portfolio.print_summary()

    # ------------------------------------------------------------------
    # Agent Threads
    # ------------------------------------------------------------------

    def _run_market_scanner(self):
        """Runs MarketScannerAgent every MARKET_SCAN_INTERVAL seconds."""
        while self.running:
            try:
                now = time.time()
                if now - self.last_market_scan >= self.market_scan_interval:
                    logger.info("[MarketScanner] Scanning markets...")
                    # Refresh market data
                    self.polymarket.fetch_active_markets(limit=200)
                    # Get signal
                    signal = self.scanner.get_signal()
                    self.signal_queue.put(signal)
                    self.last_market_scan = now
            except Exception as e:
                logger.error(f"MarketScanner error: {e}")
            time.sleep(10)

    def _run_news_agent(self):
        """Runs NewsAnalystAgent every NEWS_POLL_INTERVAL seconds."""
        while self.running:
            try:
                now = time.time()
                if now - self.last_news_scan >= self.news_scan_interval:
                    logger.info("[NewsAgent] Scanning headlines...")
                    signal = self.news_agent.get_signal()
                    self.signal_queue.put(signal)
                    self.last_news_scan = now
            except Exception as e:
                logger.error(f"NewsAgent error: {e}")
            time.sleep(15)

    def _run_risk_monitor(self):
        """Runs RiskManager every 30 seconds to check for halt conditions."""
        while self.running:
            try:
                signal = self.risk_manager.get_signal()
                if signal["signal_type"] == "HALT":
                    logger.warning(f"RISK HALT: {signal['reason']}")
                    self.signal_queue.put(signal)
            except Exception as e:
                logger.error(f"RiskMonitor error: {e}")
            time.sleep(30)

    def _run_binance_prices(self):
        """Fetches Binance prices every 5 seconds via REST (WebSocket alternative)."""
        while self.running:
            try:
                prices = self.binance.fetch_all_prices_rest()
                # Update arbitrage agent with latest prices
                for symbol, price in prices.items():
                    self.arbitrage.update_price(symbol, price)
                # Check for arbitrage signal after every price update
                arb_signal = self.arbitrage.get_signal()
                if arb_signal["signal_type"] == "TRADE":
                    self.signal_queue.put(arb_signal)
            except Exception as e:
                logger.error(f"BinancePrices error: {e}")
            time.sleep(5)

    # ------------------------------------------------------------------
    # Signal Processing & Consensus
    # ------------------------------------------------------------------

    def _run_signal_processor(self):
        """Reads from signal queue and processes trades."""
        while self.running:
            try:
                signal = self.signal_queue.get(timeout=5)
                self._process_signal(signal)
            except Empty:
                pass
            except Exception as e:
                logger.error(f"SignalProcessor error: {e}")

    def _process_signal(self, signal: dict):
        """Process one signal — check consensus, validate, execute."""
        signal_type = signal.get("signal_type", "SKIP")

        # Hard halt — stop all trading
        if signal_type == "HALT":
            logger.warning(f"HALT signal received: {signal.get('reason')}")
            return

        # Skip non-trade signals
        if signal_type != "TRADE":
            return

        condition_id = signal.get("condition_id")
        if not condition_id:
            return

        # Add to recent signals buffer
        signal["timestamp"] = time.time()
        self.recent_signals.append(signal)

        # Clean old signals (outside consensus window)
        cutoff = time.time() - self.consensus_window
        self.recent_signals = [s for s in self.recent_signals
                               if s.get("timestamp", 0) > cutoff]

        # Check consensus: do 2+ agents agree on this market + direction?
        consensus = self._check_consensus(condition_id, signal.get("direction"))
        if len(consensus) < self.min_consensus:
            logger.info(
                f"Signal from {signal['agent']} for {condition_id[:20]} "
                f"— waiting for consensus ({len(consensus)}/{self.min_consensus})"
            )
            return

        logger.info(
            f"CONSENSUS REACHED: {len(consensus)} agents agree on "
            f"{condition_id[:20]} {signal.get('direction')} "
            f"— proceeding to trade"
        )

        self._execute_trade(signal, consensus)

    def _check_consensus(self, condition_id: str, direction: str) -> list:
        """
        Find all recent signals agreeing on this market + direction.
        Returns list of agreeing signals (deduplicated by agent).
        """
        agreeing = {}
        for s in self.recent_signals:
            if (s.get("condition_id") == condition_id and
                    s.get("direction") == direction and
                    s.get("signal_type") == "TRADE"):
                agent = s.get("agent", "unknown")
                # Keep most recent signal per agent
                if agent not in agreeing or s["timestamp"] > agreeing[agent]["timestamp"]:
                    agreeing[agent] = s
        return list(agreeing.values())

    def _execute_trade(self, signal: dict, consensus: list):
        """Validate and execute a paper trade."""
        condition_id = signal.get("condition_id")
        direction    = signal.get("direction")

        # Average edge and confidence across agreeing agents
        avg_edge       = sum(s.get("edge_pct", 0) for s in consensus) / len(consensus)
        avg_confidence = sum(s.get("confidence", 0) for s in consensus) / len(consensus)

        # Size the position
        proposed_size = self.position_sizer.calculate_size(
            bankroll=self.portfolio.bankroll,
            edge_pct=avg_edge,
            confidence=avg_confidence
        )

        if proposed_size <= 0:
            logger.info("Position sizer returned $0 — skipping trade")
            return

        # Risk check
        agent_sources = "+".join(s["agent"] for s in consensus)
        validation = self.risk_manager.validate_trade(signal, proposed_size)

        if not validation["approved"]:
            logger.info(f"Trade rejected by RiskManager: {validation['reason']}")
            return

        final_size = validation["adjusted_size"]

        # Get market details for logging
        from core.database import get_active_markets
        markets = {m.get("condition_id", m.get("id","")): m
                   for m in get_active_markets(limit=200)}
        market  = markets.get(condition_id, {})
        question = market.get("question", condition_id[:50])
        price   = market.get("last_price_yes", 0.5) or 0.5
        if direction == "NO":
            price = 1 - price

        # Execute the paper trade
        trade = self.portfolio.open_position(
            condition_id=condition_id,
            question=question,
            direction=direction,
            size_usdc=final_size,
            market_price=price,
            order_type="TAKER",
            agent_source=agent_sources
        )

        if "error" in trade:
            logger.error(f"Trade failed: {trade['error']}")
        else:
            logger.info(
                f"TRADE EXECUTED by [{agent_sources}]: "
                f"{direction} ${final_size:.2f} @ {price:.3f} "
                f"edge={avg_edge:.1f}%"
            )
            # Clear signals for this market to avoid duplicate trades
            self.recent_signals = [
                s for s in self.recent_signals
                if s.get("condition_id") != condition_id
            ]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _print_status(self):
        stats = self.portfolio.get_stats()
        logger.info(
            f"STATUS | Bankroll: ${stats['bankroll']:,.2f} "
            f"| PnL: ${stats['total_pnl']:+.2f} ({stats['total_pnl_pct']:+.1f}%) "
            f"| Open: {stats['open_positions']} "
            f"| Trades: {stats['total_trades']} "
            f"| Win rate: {stats.get('win_rate', 0):.0f}%"
        )
