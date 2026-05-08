"""
tests/test_all.py
Runs all components in isolation to verify they work.
Run this FIRST before running run.py.
No real API calls - uses mock data where needed.
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

print("="*60)
print("  POLYMARKET BOT - COMPONENT TESTS")
print("="*60)

errors = []


def test(name, fn):
    try:
        fn()
        print(f"  ✅  {name}")
    except Exception as e:
        print(f"  ❌  {name}: {e}")
        errors.append((name, str(e)))


# ------------------------------------------------------------------
# Test 1: Database
# ------------------------------------------------------------------
def test_database():
    from core.database import init_database, save_signal, get_recent_signals
    init_database()
    save_signal("TestAgent", "test-market-001", "INFO",
                direction="YES", confidence=0.8, edge_pct=5.0,
                raw_data={"test": True})
    signals = get_recent_signals(limit=5)
    assert len(signals) > 0, "No signals returned after save"

test("Database init + save/read signals", test_database)


# ------------------------------------------------------------------
# Test 2: Portfolio
# ------------------------------------------------------------------
def test_portfolio():
    from core.portfolio import VirtualPortfolio
    p = VirtualPortfolio(starting_bankroll=1000.0)
    assert p.bankroll == 1000.0

    # Open a trade
    trade = p.open_position(
        condition_id="test-001",
        question="Will BTC be higher in 15 minutes?",
        direction="YES",
        size_usdc=50.0,
        market_price=0.65,
        order_type="TAKER",
        agent_source="TestAgent"
    )
    assert "error" not in trade, f"Trade failed: {trade}"
    assert p.bankroll < 1000.0, "Bankroll should have decreased"

    # Close at win
    trade_id = trade["trade_id"]
    closed = p.close_position(trade_id, resolution_price=1.0)
    assert closed.get("pnl") is not None

    stats = p.get_stats()
    assert stats["total_trades"] == 1
    print(f"       Trade PnL: ${stats['total_pnl']:+.2f}")

test("Portfolio: open/close trade + stats", test_portfolio)


# ------------------------------------------------------------------
# Test 3: Market Scanner Agent
# ------------------------------------------------------------------
def test_market_scanner():
    from core.database import init_database, save_markets
    from agents.agents import MarketScannerAgent

    init_database()
    # Inject mock market data
    mock_markets = [
        {
            "condition_id": "mock-btc-001",
            "question": "Will Bitcoin be higher in 15 minutes?",
            "category": "crypto",
            "volume": 50000,
            "clobTokenIds": ["token-yes-001", "token-no-001"],
            "outcomePrices": ["0.55", "0.45"],
            "endDate": "2026-12-31",
            "active": True
        }
    ]
    save_markets(mock_markets)

    agent = MarketScannerAgent()
    signal = agent.get_signal()
    assert "agent" in signal
    assert "signal_type" in signal
    print(f"       Signal type: {signal['signal_type']}")
    print(f"       Reason: {signal['reason'][:60]}")

test("MarketScannerAgent: get_signal()", test_market_scanner)


# ------------------------------------------------------------------
# Test 4: Arbitrage Agent
# ------------------------------------------------------------------
def test_arbitrage_agent():
    from agents.agents import ArbitrageAgent
    agent = ArbitrageAgent()

    # Simulate BTC pumping +0.8% over 60 seconds
    import time
    base_price = 85000.0
    for i in range(10):
        agent.update_price("BTCUSDT", base_price + i * 60)
    agent.price_history["BTCUSDT"][0] = (time.time() - 90, base_price)

    momentum = agent.get_momentum("BTCUSDT", 60)
    print(f"       Simulated momentum: {momentum:+.2f}%")
    prob = agent.momentum_to_probability(momentum)
    print(f"       Implied probability: {prob:.0%}")
    assert 0 < prob < 1

test("ArbitrageAgent: momentum calculation", test_arbitrage_agent)


# ------------------------------------------------------------------
# Test 5: News Fetcher (keyword scoring - no API needed)
# ------------------------------------------------------------------
def test_news_fetcher():
    from data.news_fetcher import NewsFetcher
    nf = NewsFetcher()

    headline = "Bitcoin surges past $90,000 after Federal Reserve signals rate pause"
    result = nf.score_headline(headline)
    print(f"       Headline score: {result['score']}/10")
    print(f"       Direction: {result['direction']} ({result['confidence']:.0%})")
    print(f"       Keywords: {result['keywords_found']}")
    assert result["score"] > 5, "Should be high relevance"
    assert result["direction"] == "BULLISH"

test("NewsFetcher: keyword scoring (no API needed)", test_news_fetcher)


# ------------------------------------------------------------------
# Test 6: Risk Manager
# ------------------------------------------------------------------
def test_risk_manager():
    from agents.agents import RiskManagerAgent
    from core.portfolio import VirtualPortfolio

    p = VirtualPortfolio(1000.0)
    rm = RiskManagerAgent(portfolio=p)

    # Should approve small trade
    result = rm.validate_trade({"condition_id": "test"}, proposed_size=30.0)
    assert result["approved"], f"Should approve: {result['reason']}"
    print(f"       $30 trade: {result['reason']}")

    # Should reject oversized trade
    result2 = rm.validate_trade({"condition_id": "test"}, proposed_size=200.0)
    assert result2["adjusted_size"] <= 50.0, "Should cap at 5% of $1000"
    print(f"       $200 trade adjusted to: ${result2['adjusted_size']:.2f}")

test("RiskManagerAgent: validate trades", test_risk_manager)


# ------------------------------------------------------------------
# Test 7: Position Sizer (Kelly)
# ------------------------------------------------------------------
def test_position_sizer():
    from agents.agents import PositionSizerAgent
    ps = PositionSizerAgent()

    size = ps.calculate_size(bankroll=1000.0, edge_pct=15.0, confidence=0.8)
    print(f"       15% edge, 80% confidence → ${size:.2f}")
    assert 5 <= size <= 50, f"Unexpected size: {size}"

    size2 = ps.calculate_size(bankroll=1000.0, edge_pct=0, confidence=0)
    assert size2 == 0, "Zero edge should return $0"
    print(f"       0% edge → ${size2:.2f}")

test("PositionSizerAgent: Kelly sizing", test_position_sizer)


# ------------------------------------------------------------------
# Test 8: Full mock trade flow
# ------------------------------------------------------------------
def test_full_flow():
    from core.database import init_database, save_markets
    from core.portfolio import VirtualPortfolio
    from agents.agents import (MarketScannerAgent, RiskManagerAgent,
                                PositionSizerAgent)

    init_database()
    save_markets([{
        "condition_id": "flow-test-001",
        "question": "Will BTC be higher in 15 minutes? (flow test)",
        "category": "crypto",
        "volume": 80000,
        "clobTokenIds": ["tok-yes", "tok-no"],
        "outcomePrices": ["0.60", "0.40"],
        "endDate": "2026-12-31"
    }])

    portfolio = VirtualPortfolio(1000.0)
    scanner   = MarketScannerAgent()
    risk      = RiskManagerAgent(portfolio=portfolio)
    sizer     = PositionSizerAgent(portfolio=portfolio)

    signal = scanner.get_signal()
    if signal["signal_type"] == "TRADE":
        size = sizer.calculate_size(1000.0, edge_pct=10.0, confidence=0.7)
        validation = risk.validate_trade(signal, size)
        if validation["approved"]:
            trade = portfolio.open_position(
                condition_id=signal["condition_id"],
                question="Test market",
                direction=signal["direction"],
                size_usdc=validation["adjusted_size"],
                market_price=0.60,
                agent_source="TestFlow"
            )
            assert "error" not in trade
            print(f"       Trade opened: ${trade['size_usdc']:.2f} "
                  f"@ {trade['fill_price']:.3f}")
            closed = portfolio.close_position(trade["trade_id"], 1.0)
            print(f"       Trade closed: PnL ${closed['pnl']:+.2f}")

test("Full flow: scanner → risk → sizer → portfolio", test_full_flow)


# ------------------------------------------------------------------
# Results
# ------------------------------------------------------------------
print()
print("="*60)
if errors:
    print(f"  {len(errors)} test(s) FAILED:")
    for name, err in errors:
        print(f"    • {name}: {err}")
    print()
    print("  Fix the errors above before running run.py")
else:
    print("  All tests passed! ✅")
    print()
    print("  Next steps:")
    print("  1. Copy .env.example to .env")
    print("  2. Add NEWS_API_KEY (free at newsapi.org)")
    print("  3. Optionally add ANTHROPIC_API_KEY for AI news analysis")
    print("  4. Run: python run.py")
    print("  5. Open dashboard: streamlit run dashboard/app.py")
print("="*60)
