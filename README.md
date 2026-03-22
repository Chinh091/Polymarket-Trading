# Polymarket Paper Trading Bot

A multi-agent paper trading simulation for Polymarket prediction markets.
Uses real live market data but executes all trades with virtual money.
Built to gather 30+ days of clean performance data before risking real capital.

**Status: Paper trading only. No real funds are ever touched.**

---

## What This Bot Does

Polymarket is a prediction market where you buy YES/NO contracts on real-world events.
Prices range from $0.01 to $0.99 and represent the market's implied probability.
A YES contract bought at $0.60 pays $1.00 if the event happens — that's a 67% return.

This bot runs five specialist agents in parallel. Each agent watches one data source
and emits a signal when it sees an opportunity. The orchestrator requires at least
two agents to agree before placing a paper trade. Every trade simulates exact
real-world costs: 1.56% taker fee, $0.02 gas, and realistic slippage.

The goal is to find out which strategy generates real edge before going live.

---

## Architecture

```
Data Sources (Layer 0)
  Polymarket Gamma API   →  market discovery, volumes, questions
  Polymarket CLOB API    →  live orderbook depth, bid/ask prices
  Binance WebSocket      →  real-time BTC/ETH/SOL spot prices
  NewsAPI                →  headlines (free tier, 100 calls/day)

Specialist Agents (Layer 1) — each owns one job
  MarketScannerAgent     →  finds high-volume, liquid markets
  ArbitrageAgent         →  detects Binance momentum vs Polymarket lag
  NewsAnalystAgent       →  scores headlines for market impact
  RiskManagerAgent       →  enforces drawdown/position limits
  PositionSizerAgent     →  quarter-Kelly criterion sizing

Orchestrator (Layer 2)
  MasterOrchestrator     →  consensus logic, trade approval, routing

Paper Trading Engine (Layer 3)
  VirtualPortfolio       →  $1,000 virtual USDC, exact fee simulation
  Trade Logger           →  all trades stored to SQLite

Dashboard (Layer 4)
  Streamlit app          →  live PnL curve, signals, open positions
```

---

## File Structure

```
polymarket_bot/
│
├── run.py                    Entry point. Starts all agents and orchestrator.
├── requirements.txt          All Python dependencies.
├── .env.example              Config template. Copy to .env before running.
│
├── core/
│   ├── database.py           SQLite schema and all read/write functions.
│   │                         Tables: markets, price_snapshots, spot_prices,
│   │                                 agent_signals, paper_trades,
│   │                                 portfolio_snapshots, news_log
│   │
│   ├── portfolio.py          VirtualPortfolio class.
│   │                         open_position() — simulates a buy with exact fees
│   │                         close_position() — resolves trade, calculates PnL
│   │                         get_stats() — returns win rate, Sharpe, drawdown
│   │
│   ├── orchestrator.py       MasterOrchestrator class.
│   │                         Runs each agent in a background thread.
│   │                         Collects signals via Python Queue.
│   │                         Fires trades when 2+ agents agree (consensus).
│   │
│   └── logger.py             Shared logger. Writes to logs/bot_YYYYMMDD.log
│                             and stdout simultaneously.
│
├── agents/
│   └── agents.py             All five agents in one file.
│
│       BaseAgent             Parent class. Defines get_signal() contract.
│                             Every agent returns:
│                             { agent, condition_id, signal_type, direction,
│                               confidence (0-1), edge_pct, reason, data }
│
│       MarketScannerAgent    Reads markets table from DB.
│                             Scores by: volume (60%) + uncertainty (40%).
│                             Filters: volume > $10k, price between 0.05-0.95.
│                             Runs every MARKET_SCAN_INTERVAL seconds (default 60).
│
│       ArbitrageAgent        Tracks BTC/ETH/SOL price history in memory.
│                             Calculates momentum over last 60 seconds.
│                             Converts momentum % to implied probability.
│                             Flags when edge vs Polymarket price > 5% after fees.
│                             update_price(symbol, price) called on every tick.
│
│       NewsAnalystAgent      Fetches headlines from NewsAPI.
│                             Scores by keyword matching (no API key needed).
│                             Upgrades to Claude Haiku if ANTHROPIC_API_KEY set.
│                             Only signals when score >= 7/10 AND confidence > 0.6.
│
│       RiskManagerAgent      validate_trade(signal, size) → {approved, reason, adjusted_size}
│                             Hard rules: max drawdown halt, max 3 open positions.
│                             Soft rules: caps position at 5% of bankroll.
│                             get_signal() monitors continuously, emits HALT if needed.
│
│       PositionSizerAgent    calculate_size(bankroll, edge_pct, confidence) → float
│                             Formula: Kelly fraction = edge × confidence × 0.25
│                             Bounds: min $5, max 5% of bankroll.
│
├── data/
│   ├── polymarket_fetcher.py PolymarketFetcher class.
│   │                         fetch_active_markets() — Gamma API, returns list
│   │                         fetch_crypto_markets() — filters for 15-min markets
│   │                         fetch_orderbook(token_id) — CLOB orderbook depth
│   │                         fetch_spread(token_id) — best bid/ask
│   │                         stream_prices(token_ids, callback) — async WebSocket
│   │                         get_implied_probability(token_id) — mid price = probability
│   │
│   ├── binance_fetcher.py    BinanceFetcher class.
│   │                         fetch_all_prices_rest() — one-shot REST fetch
│   │                         stream_prices(callback) — async WebSocket stream
│   │                         Tracks: BTCUSDT, ETHUSDT, SOLUSDT
│   │                         No API key required (public endpoints).
│   │
│   └── news_fetcher.py       NewsFetcher class.
│                             fetch_headlines(query) — NewsAPI REST call
│                             score_headline(headline) → {score, direction, confidence}
│                             analyse_with_ai(headline) — Claude Haiku (optional)
│                             process_and_store(headlines) — scores + saves to DB
│                             Works fully without any API keys (keyword scoring).
│
├── dashboard/
│   └── app.py                Streamlit dashboard. Auto-refreshes every 30s.
│                             Shows: bankroll curve, PnL, open positions,
│                             agent signals feed, trade history, active markets.
│
├── data/
│   └── polymarket_bot.db     SQLite database (created automatically on first run).
│
├── logs/
│   └── bot_YYYYMMDD.log      Daily rotating log file.
│
└── tests/
    └── test_all.py           8 component tests. Run before run.py to verify setup.
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — add NEWS_API_KEY at minimum (free at newsapi.org)

# 3. Verify everything works
python tests/test_all.py
# All 8 tests should pass

# 4. Start the bot (Terminal 1)
python run.py

# 5. Open dashboard (Terminal 2)
streamlit run dashboard/app.py
```

---

## Environment Variables (.env)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEWS_API_KEY` | Recommended | — | Free at newsapi.org. 100 requests/day on free tier. Without it, bot uses mock headlines. |
| `ANTHROPIC_API_KEY` | Optional | — | Enables Claude Haiku news analysis. Free $5 credit at platform.anthropic.com. Without it, keyword scoring is used instead. |
| `STARTING_BANKROLL` | No | 1000.0 | Virtual USDC starting balance |
| `MAX_POSITION_PCT` | No | 0.05 | Max single position as % of bankroll (5%) |
| `MAX_OPEN_POSITIONS` | No | 3 | Max concurrent open trades |
| `MAX_DRAWDOWN_PCT` | No | 0.15 | Halt trading if drawdown exceeds this (15%) |
| `MARKET_SCAN_INTERVAL` | No | 60 | Seconds between market scans |
| `NEWS_POLL_INTERVAL` | No | 60 | Seconds between news fetches |

---

## Fee Simulation (1:1 with real Polymarket)

Every paper trade applies exact real-world costs so data is accurate.

```
Taker order (market buy/sell):  1.56% of trade size
Maker order (limit order):       0.00% (earns rebate in reality)
Gas cost per transaction:        $0.02 (Polygon network)
Slippage (< $50 order):         ~0.1%
Slippage ($50–$200 order):      ~0.5%
Slippage ($200–$1000 order):    ~1.0%
Slippage (> $1000 order):       ~2.0%
Slippage with real orderbook:   walks book for exact fill price
```

---

## Agent Signal Contract

Every agent's `get_signal()` returns this exact dict:

```python
{
    "agent":        str,          # Agent name e.g. "ArbitrageAgent"
    "condition_id": str | None,   # Polymarket market ID
    "signal_type":  str,          # "TRADE" | "SKIP" | "HALT" | "INFO"
    "direction":    str | None,   # "YES" | "NO" | None
    "confidence":   float,        # 0.0 to 1.0
    "edge_pct":     float,        # Expected edge % after fees
    "reason":       str,          # Human-readable explanation
    "data":         dict          # Agent-specific extra data
}
```

To add a new agent: subclass `BaseAgent`, implement `get_signal()`,
instantiate in `MasterOrchestrator.__init__()`, add a thread in `start()`.

---

## Consensus Logic

The orchestrator requires **2 or more agents** to agree before placing a trade.
"Agree" means: same `condition_id` + same `direction` within a 5-minute window.

```
ArbitrageAgent  →  TRADE YES on condition_id="abc123"  (edge 12%)
MarketScanner   →  TRADE YES on condition_id="abc123"  (score 0.80)
                                    ↓
              Consensus: 2 agents agree → proceed
                                    ↓
              RiskManager validates size and drawdown
                                    ↓
              PositionSizer calculates quarter-Kelly amount
                                    ↓
              VirtualPortfolio.open_position() executes
```

To change the threshold: set `self.min_consensus` in `MasterOrchestrator`.

---

## Database Schema

All data lives in `data/polymarket_bot.db` (SQLite).

| Table | What it stores |
|---|---|
| `markets` | Active Polymarket markets with prices and volumes |
| `price_snapshots` | Orderbook snapshots (bid/ask/spread) per market per tick |
| `spot_prices` | Binance BTC/ETH/SOL price history |
| `agent_signals` | Every signal from every agent with timestamp |
| `paper_trades` | Full trade record: entry, exit, fees, PnL, agent source |
| `portfolio_snapshots` | Bankroll value over time (for PnL curve) |
| `news_log` | All headlines with relevance score and AI signal |

Query example:
```bash
sqlite3 data/polymarket_bot.db "SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT 10;"
```

---

## Key Design Decisions

**Why quarter-Kelly?** Full Kelly maximises long-run growth but requires a perfectly
calibrated edge estimate. Since we're estimating edge from imperfect signals,
quarter-Kelly provides a 75% safety margin against over-betting.

**Why SQLite?** Simplicity for paper trading phase. Zero setup, portable,
queryable with any tool. Upgrade to Postgres when going live.

**Why keyword scoring without AI?** The bot is fully functional without any
API keys. NewsAPI + keyword scoring is good enough to identify high-impact
headlines. Claude Haiku is an optional upgrade for better direction accuracy.

**Why consensus from 2 agents?** Single-agent signals have high false-positive
rates. Requiring two independent signals to agree on the same market
significantly reduces noise trades. The 5-minute window is wide enough to
catch agents that scan at different intervals.

**Why threads not asyncio?** Simplicity. Each agent runs in its own thread
with its own error handling. One agent crashing doesn't kill the others.
The signal queue is the only shared state.

---

## Extending the Bot

### Add a new agent

```python
# agents/agents.py
class MyNewAgent(BaseAgent):
    def __init__(self):
        super().__init__("MyNewAgent")

    def get_signal(self) -> dict:
        # your logic here
        if found_opportunity:
            return self._trade_signal(
                condition_id="abc123",
                direction="YES",
                confidence=0.75,
                edge_pct=8.0,
                reason="My reason",
                data={"extra": "info"}
            )
        return self._no_signal("No opportunity found")
```

Then in `core/orchestrator.py`, add to `__init__`:
```python
self.my_agent = MyNewAgent()
```

And add a thread in `start()`:
```python
Thread(target=self._run_my_agent, daemon=True, name="MyAgent").start()
```

### Change consensus threshold

```python
# core/orchestrator.py
self.min_consensus = 3      # require 3 agents instead of 2
self.consensus_window = 600 # extend window to 10 minutes
```

### Upgrade to live trading (future)

Replace `VirtualPortfolio.open_position()` with a real CLOB order:
```python
from py_clob_client.client import ClobClient
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137)
# place real order here
```
**Only do this after 30+ days of profitable paper trading.**

---

## Running Tests

```bash
python tests/test_all.py
```

Tests cover: database init, portfolio open/close, market scanner,
arbitrage momentum, news keyword scoring, risk validation,
Kelly sizing, and full end-to-end trade flow.

All tests use mock data — no real API calls, no network required.

---

## What to Review After 30 Days

Open `data/polymarket_bot.db` and check:

```sql
-- Which agent generates the most profitable trades?
SELECT agent_source, COUNT(*) trades, SUM(pnl) total_pnl, AVG(pnl) avg_pnl
FROM paper_trades WHERE status='closed'
GROUP BY agent_source ORDER BY total_pnl DESC;

-- What's the win rate per direction?
SELECT direction, COUNT(*) trades,
       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins
FROM paper_trades WHERE status='closed'
GROUP BY direction;

-- How much are fees eating into profits?
SELECT SUM(taker_fee + gas_cost) total_fees, SUM(pnl) total_pnl,
       SUM(taker_fee + gas_cost) / ABS(SUM(pnl)) fee_ratio
FROM paper_trades WHERE status='closed';
```

---

## Warnings

- This is a paper trading simulator. It does not place real trades.
- Polymarket is geo-restricted — check your local regulations before live trading.
- Past paper trading performance does not guarantee live trading performance.
- The arbitrage window on Polymarket has compressed significantly since 2024.
  Edge estimates from the ArbitrageAgent are approximate, not guaranteed.
- Never trade more than you can afford to lose.
