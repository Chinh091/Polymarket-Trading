"""
core/orchestrator.py
Master Orchestrator - collects signals from all agents,
requires consensus before approving trades, routes to portfolio.
"""
import asyncio
import time
import os
import logging
from datetime import datetime
from queue import Queue, Empty
from threading import Thread

import json

# Auto-select Supabase when SUPABASE_URL is configured, else fall back to SQLite
if os.getenv("SUPABASE_URL"):
    from core.supabase_db import (
        init_database, save_signal, save_trade_journal,
        save_markets, save_price_snapshot, save_portfolio_snapshot,
        get_open_trades, get_trade_history, get_strategy_params
    )
    logger_db = "supabase"
else:
    from core.database import init_database, save_signal, save_trade_journal
    logger_db = "sqlite"

from core.logger import setup_logger
from core.portfolio import VirtualPortfolio
from agents.agents import (
    MarketScannerAgent, ArbitrageAgent,
    NewsAnalystAgent, RiskManagerAgent, PositionSizerAgent,
    OTMOpportunityAgent, BayesPriorAgent
)
from data.polymarket_fetcher import PolymarketFetcher
from data.binance_fetcher import BinanceFetcher
from data.news_fetcher import NewsFetcher

logger = setup_logger("Orchestrator")
logger.info(f"Database backend: {logger_db}")


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
        self.otm_agent      = OTMOpportunityAgent(arbitrage_agent=self.arbitrage)
        self.bayes_agent    = BayesPriorAgent(arbitrage_agent=self.arbitrage)

        # Recent signals buffer for consensus check
        self.recent_signals = []  # list of signal dicts from last 5 minutes
        self.consensus_window = 300  # 5 minutes
        self.min_consensus   = 2     # need 2 agents to agree

        # Throttles
        self.last_market_scan   = 0
        self.last_news_scan     = 0
        self.market_scan_interval = int(os.getenv("MARKET_SCAN_INTERVAL", 60))
        self.news_scan_interval   = int(os.getenv("NEWS_POLL_INTERVAL", 60))

        logger.info("Orchestrator initialised - all agents ready")

    # ------------------------------------------------------------------
    # Main Entry Point
    # ------------------------------------------------------------------

    def start(self):
        """Start the bot - runs all agents in background threads."""
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
            Thread(target=self._run_otm_agent,       daemon=True, name="OTMOpportunity"),
            Thread(target=self._run_bayes_agent,     daemon=True, name="BayesPrior"),
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
            # Auto-generate end-of-day report on exit
            try:
                from reports.generate_report import save_report
                save_report()
            except Exception as e:
                logger.error(f"Could not generate report: {e}")

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

    def _call_opus_gate(self, question: str, direction: str, size: float,
                        price: float, avg_edge: float, avg_confidence: float,
                        consensus: list) -> dict:
        """
        Opus 4.6 is the mandatory final decision maker.
        Only APPROVE passes - REJECT or any error drops the trade.
        Falls back to APPROVE only if no API key is configured.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("No ANTHROPIC_API_KEY - Opus gate bypassed, auto-approving")
            return {"verdict": "APPROVE", "reasoning": "No API key configured."}

        agent_lines = "\n".join([
            f"  • {s.get('agent')}: {s.get('reason', '')} "
            f"(edge={s.get('edge_pct', 0):.1f}%, conf={s.get('confidence', 0):.0%})"
            for s in consensus
        ])

        prompt = f"""You are the automated risk gate for a Polymarket paper trading bot.
Your decision is final and binary - no hedging allowed.

PROPOSED TRADE
  Market    : {question}
  Direction : BUY {direction} contracts
  Size      : ${size:.2f} USDC (paper money)
  Entry     : {price:.3f} ({price * 100:.1f}% implied probability)
  Edge est. : {avg_edge:.1f}% after 1.56% taker fee
  Confidence: {avg_confidence:.0%}

AGENT SIGNALS (≥2 agreed to trigger):
{agent_lines}

APPROVE if: edge is credibly positive, agent reasoning is internally consistent,
            direction matches the stated logic, size is proportionate to confidence.

REJECT if:  edge looks fabricated or trivially small, reasoning contradicts itself,
            direction and logic don't match, or signal is obvious noise.

Reply with ONLY valid JSON - no markdown, no explanation outside the JSON:
{{"verdict": "APPROVE", "reasoning": "1-2 sentences max"}}

Verdict must be exactly APPROVE or REJECT. UNCERTAIN is not a valid answer."""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}]
            )
            text = msg.content[0].text.strip()
            result = json.loads(text)
            # Enforce binary - anything other than APPROVE is a REJECT
            if result.get("verdict") not in ("APPROVE", "REJECT"):
                result["verdict"] = "REJECT"
                result["reasoning"] = f"Unexpected verdict normalised to REJECT: {text[:60]}"
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Opus gate returned non-JSON: {e} - rejecting to be safe")
            return {"verdict": "REJECT", "reasoning": f"Parse error: {e}"}
        except Exception as e:
            logger.error(f"Opus gate API error: {e} - rejecting to be safe")
            return {"verdict": "REJECT", "reasoning": f"API error: {e}"}

    def _run_bayes_agent(self):
        """Runs BayesPriorAgent every 2 minutes."""
        while self.running:
            try:
                logger.info("[BayesPrior] Running Bayes prior mapping scan...")
                signal = self.bayes_agent.get_signal()
                if signal["signal_type"] == "TRADE":
                    self.signal_queue.put(signal)
            except Exception as e:
                logger.error(f"BayesAgent error: {e}")
            time.sleep(120)

    def _run_otm_agent(self):
        """Runs OTMOpportunityAgent every 90 seconds."""
        while self.running:
            try:
                logger.info("[OTMOpportunity] Scanning for underpriced OTM contracts...")
                signal = self.otm_agent.get_signal()
                if signal["signal_type"] == "TRADE":
                    self.signal_queue.put(signal)
            except Exception as e:
                logger.error(f"OTMAgent error: {e}")
            time.sleep(90)

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
        """Process one signal - check consensus, validate, execute."""
        signal_type = signal.get("signal_type", "SKIP")

        # Hard halt - stop all trading
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
                f"- waiting for consensus ({len(consensus)}/{self.min_consensus})"
            )
            return

        logger.info(
            f"CONSENSUS REACHED: {len(consensus)} agents agree on "
            f"{condition_id[:20]} {signal.get('direction')} "
            f"- proceeding to trade"
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
        """Validate and execute a paper trade. Journals every decision."""
        condition_id = signal.get("condition_id")
        direction    = signal.get("direction")

        # Average edge and confidence across agreeing agents
        avg_edge       = sum(s.get("edge_pct", 0) for s in consensus) / len(consensus)
        avg_confidence = sum(s.get("confidence", 0) for s in consensus) / len(consensus)

        # Shared agent metadata for journaling
        agent_sources  = "+".join(s["agent"] for s in consensus)
        agent_signals_json = json.dumps([{
            "agent":      s.get("agent"),
            "reason":     s.get("reason"),
            "edge_pct":   s.get("edge_pct"),
            "confidence": s.get("confidence"),
            "direction":  s.get("direction"),
            "data":       s.get("data", {}),
        } for s in consensus])

        # Size the position
        proposed_size = self.position_sizer.calculate_size(
            bankroll=self.portfolio.bankroll,
            edge_pct=avg_edge,
            confidence=avg_confidence
        )

        if proposed_size <= 0:
            logger.info("Position sizer returned $0 - skipping trade")
            return

        # Risk check
        validation = self.risk_manager.validate_trade(signal, proposed_size)
        if not validation["approved"]:
            logger.info(f"Trade rejected by RiskManager: {validation['reason']}")
            save_trade_journal(
                condition_id=condition_id, question=signal.get("reason", "")[:100],
                direction=direction, proposed_size=proposed_size, entry_price=0,
                agent_sources=agent_sources, agent_signals=agent_signals_json,
                opus_verdict="N/A", opus_reasoning=f"RiskManager: {validation['reason']}",
                outcome="rejected_risk", avg_edge=avg_edge, avg_confidence=avg_confidence
            )
            return

        final_size = validation["adjusted_size"]

        # Get market details
        from core.database import get_active_markets
        markets  = {m.get("condition_id", m.get("id","")): m
                    for m in get_active_markets(limit=200)}
        market   = markets.get(condition_id, {})
        question = market.get("question", condition_id[:50])
        price    = market.get("last_price_yes", 0.5) or 0.5
        if direction == "NO":
            price = 1 - price
        market_volume = market.get("volume", 0) or 0

        # ── Opus 4.6 Automated Gate ──────────────────────────────────────
        logger.info(
            f"Asking Opus 4.6 to decide: {direction} ${final_size:.2f} "
            f"on {question[:50]}"
        )
        gate = self._call_opus_gate(
            question=question, direction=direction, size=final_size,
            price=price, avg_edge=avg_edge, avg_confidence=avg_confidence,
            consensus=consensus
        )

        if gate["verdict"] != "APPROVE":
            logger.info(
                f"Opus 4.6 REJECTED: {gate.get('reasoning','')[:120]} - trade dropped"
            )
            save_trade_journal(
                condition_id=condition_id, question=question, direction=direction,
                proposed_size=final_size, entry_price=price,
                agent_sources=agent_sources, agent_signals=agent_signals_json,
                opus_verdict=gate["verdict"], opus_reasoning=gate.get("reasoning", ""),
                outcome="rejected_opus", avg_edge=avg_edge, avg_confidence=avg_confidence,
                market_volume=market_volume
            )
            self.recent_signals = [
                s for s in self.recent_signals
                if s.get("condition_id") != condition_id
            ]
            return

        logger.info(f"Opus 4.6 APPROVED: {gate.get('reasoning','')[:100]}")

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
                f"TRADE EXECUTED by [{agent_sources}] + Opus 4.6: "
                f"{direction} ${final_size:.2f} @ {price:.3f} edge={avg_edge:.1f}%"
            )
            # Journal the successful execution
            save_trade_journal(
                condition_id=condition_id, question=question, direction=direction,
                proposed_size=final_size, entry_price=price,
                agent_sources=agent_sources, agent_signals=agent_signals_json,
                opus_verdict="APPROVE", opus_reasoning=gate.get("reasoning", ""),
                outcome="executed", avg_edge=avg_edge, avg_confidence=avg_confidence,
                market_volume=market_volume
            )
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
