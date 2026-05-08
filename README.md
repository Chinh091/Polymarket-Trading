# Polymarket Paper Trading Bot

> Algorithmic trading system for prediction markets. Six specialist agents, built-in risk management,
> LLM-based trade approval gate, and full fee simulation. Built to validate quantitative edge
> before deploying real capital.

**Status: Paper trading only. No real funds at risk.**

---

## What This Project Demonstrates

This is a production-grade algorithmic trading system built from scratch. It covers the full
quant trading stack: signal generation, position sizing, risk management, portfolio tracking,
and performance reporting.

**Key skills demonstrated:**

- Quantitative signal design (Shannon entropy, Bayesian inference, Kelly criterion, log-odds filtering)
- Multi-agent system architecture with a Python thread-per-agent model
- Real-time data ingestion from Binance WebSocket and Polymarket REST/WebSocket APIs
- LLM integration (Claude Opus 4.6) as a final trade validation gate
- Exact fee and slippage simulation for accurate paper trading
- Dual storage backends: SQLite (local) and Supabase (hosted), switchable via env var
- Automated daily report generation with trade journal and performance analytics
- Full-stack dashboard: Streamlit (Python) and Next.js + Supabase (hosted)
- End-to-end test suite covering all core components

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Data feeds | Polymarket Gamma API, Polymarket CLOB API, Binance REST/WebSocket |
| LLM gate | Anthropic Claude Opus 4.6 (via API) |
| Storage | SQLite (default) / Supabase PostgreSQL (production) |
| Dashboard | Streamlit (local) + Next.js / Tailwind CSS (hosted) |
| News feed | NewsAPI + keyword scoring + optional Claude Haiku analysis |
| Deployment | Vercel (frontend) + Supabase (backend) |
| Testing | Python unittest, all mock data, no network required |

---

## How It Works

Polymarket is an on-chain prediction market where YES/NO contracts are priced as implied
probabilities ($0.01 to $0.99). A contract bought at $0.40 pays $1.00 if correct - a 150%
return. The market is efficient but not perfectly so: crypto price-level contracts often lag
Binance spot price movements by 30-60 seconds, creating a measurable edge window.

This bot finds and exploits those windows through six independent agents running in parallel.

### Trade Flow

```
Agents run continuously in background threads
        |
        v
Signal queue receives trade signals
        |
        v
[Gate 1] Consensus check - 2+ agents must agree on same market and direction
        |
        v
[Gate 2] Risk validation - drawdown, position count, and size limits
        |
        v
[Gate 3] Kelly position sizing - quarter-Kelly formula with confidence weighting
        |
        v
[Gate 4] Claude Opus 4.6 review - reads full context, returns APPROVE or REJECT only
        |
        v
Paper trade executed with exact fee + slippage simulation
```

---

## The Six Agents

| Agent | What it does | Mathematical model |
|---|---|---|
| MarketScanner | Scores all active markets for opportunity | Shannon entropy + liquidity score |
| ArbitrageAgent | Detects Binance momentum not yet reflected in Polymarket | Momentum-to-probability with log-odds filter |
| OTMOpportunityAgent | Finds underpriced out-of-the-money contracts (4%-10%) | Expected value ratio >= 1.8x |
| BayesPriorAgent | Applies skeptical Bayesian prior before accepting a signal | p^2 prior mapping function |
| NewsAnalystAgent | Scores news headlines for market-moving impact | Keyword scoring + optional Claude Haiku |
| RiskManagerAgent | Enforces drawdown and position limits | Hard rules: 15% drawdown halt, max 3 positions |

---

## Mathematical Models

Each agent is built on a distinct quantitative method - not heuristics.

### Shannon Entropy (Market Uncertainty Score)

```
H(p) = -p * log2(p) - (1 - p) * log2(1 - p)
```

H = 1.0 at p = 0.5 (maximum uncertainty = maximum opportunity).
H approaches 0 near p = 0 or p = 1 (market decided, no edge to extract).

### Log-Odds Filter (Noise Rejection)

```
log_odds = ln(p / (1 - p))
|log_odds| < 0.5  =>  discard (signal indistinguishable from noise)
|log_odds| >= 0.5 =>  signal is actionable
```

Rejects signals where the implied probability sits between 37.8% and 62.2% - the
zone where even a correct directional read cannot overcome the 1.56% taker fee.

### Bayesian Prior Mapping

```
prior = p^2              (skeptical prior, always < p)
M     = B - p^2          (mapping function, B = estimated true probability)
|M| > 0.06  =>  trade
```

Squaring the market price penalises low-probability events proportionally, demanding
stronger evidence before buying them as underpriced.

### Quarter-Kelly Position Sizing

```
full_kelly    = edge_decimal * confidence
quarter_kelly = full_kelly * 0.25
bet_size      = bankroll * quarter_kelly   (min $5, max 5% of bankroll)
```

Full Kelly maximises long-run growth but is catastrophic when edge estimates are noisy.
Quarter-Kelly provides the same growth direction at one quarter of the variance.

---

## Risk Management

| Rule | Limit |
|---|---|
| Maximum drawdown | 15% - full trading halt if breached |
| Maximum open positions | 3 concurrent positions |
| Maximum position size | 5% of bankroll per trade |
| Minimum position size | $5 (below this, fees eliminate the edge) |
| Consensus requirement | 2+ independent agents must agree |
| LLM gate | Claude Opus 4.6 must APPROVE every trade |

---

## Fee Simulation

Every paper trade applies exact real-world costs so 30-day performance data
translates directly to live trading expectations.

```
Taker order:              1.56% of trade size
Maker order:              0.00%
Gas per transaction:      $0.02 (Polygon network)
Slippage < $50:           +0.1%
Slippage $50-$200:        +0.5%
Slippage $200-$1,000:     +1.0%
Slippage > $1,000:        +2.0%
With real orderbook data: walks book levels for exact average fill price
```

---

## Project Structure

```
polymarket-trading/
|
+-- run.py                    Entry point
+-- requirements.txt
+-- .env.example              Config template
|
+-- core/
|   +-- orchestrator.py       Consensus logic, Opus gate, trade routing
|   +-- portfolio.py          Virtual portfolio with exact fee simulation
|   +-- database.py           SQLite schema and queries
|   +-- supabase_db.py        Supabase backend (auto-selected via env var)
|
+-- agents/
|   +-- agents.py             All six agents + BaseAgent interface
|
+-- data/
|   +-- polymarket_fetcher.py Market discovery, orderbook, WebSocket stream
|   +-- binance_fetcher.py    BTC/ETH/SOL price feed (no API key needed)
|   +-- news_fetcher.py       Headlines + keyword scoring + Haiku analysis
|
+-- research/
|   +-- strategy_researcher.py Autonomous parameter optimizer (Opus-assisted)
|
+-- reports/
|   +-- generate_report.py    Daily markdown report with full trade journal
|
+-- dashboard/
|   +-- app.py                Streamlit dashboard
|
+-- webapp/                   Next.js hosted dashboard (Vercel + Supabase)
|
+-- tests/
|   +-- test_all.py           8 component tests, no network required
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env - add NEWS_API_KEY (free at newsapi.org)

# 3. Run tests to verify setup
python tests/test_all.py

# 4. Start the bot
python run.py

# 5. Open dashboard (separate terminal)
streamlit run dashboard/app.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEWS_API_KEY` | Recommended | - | Free at newsapi.org |
| `ANTHROPIC_API_KEY` | Optional | - | Enables Opus gate + Haiku news analysis |
| `SUPABASE_URL` | Optional | - | Switches storage from SQLite to Supabase |
| `SUPABASE_KEY` | Optional | - | Supabase anon key |
| `STARTING_BANKROLL` | No | 1000.0 | Virtual USDC starting balance |
| `MAX_POSITION_PCT` | No | 0.05 | Max position size (5% of bankroll) |
| `MAX_OPEN_POSITIONS` | No | 3 | Max concurrent trades |
| `MAX_DRAWDOWN_PCT` | No | 0.15 | Drawdown halt threshold |
| `MARKET_SCAN_INTERVAL` | No | 60 | Seconds between market scans |
| `NEWS_POLL_INTERVAL` | No | 60 | Seconds between news fetches |

---

## Performance Analysis

After 30 days of paper trading, query the database to identify which strategies work:

```sql
-- Best performing agent combinations
SELECT agent_source, COUNT(*) trades, SUM(pnl) total_pnl, AVG(pnl) avg_pnl
FROM paper_trades WHERE status = 'closed'
GROUP BY agent_source ORDER BY total_pnl DESC;

-- Win rate by direction
SELECT direction, COUNT(*) trades,
       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins
FROM paper_trades WHERE status = 'closed'
GROUP BY direction;

-- Fee drag vs gross PnL
SELECT SUM(taker_fee + gas_cost) total_fees, SUM(pnl) gross_pnl
FROM paper_trades WHERE status = 'closed';
```

---

## Disclaimer

- Paper trading only. This does not place real trades.
- Polymarket is geo-restricted. Check local regulations before live trading.
- Past paper trading performance does not guarantee live performance.
- Never trade more than you can afford to lose.
