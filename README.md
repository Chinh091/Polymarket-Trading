# Polymarket Paper Trading Bot

A multi-agent, mathematically-grounded paper trading system for Polymarket prediction markets.
Runs six specialist agents in parallel, requires consensus before any trade, and routes every
decision through a Claude Opus 4.6 LLM gate before execution.

**Status: Paper trading only. No real funds are ever touched.**

---

## Overview

Polymarket is an on-chain prediction market where YES/NO contracts are priced as implied
probabilities between $0.01 and $0.99. A YES contract bought at $0.40 pays $1.00 if the
event resolves YES - a 150% return. The market is efficient but not perfectly so: there
are exploitable edges in crypto price-level contracts when spot price momentum has not yet
propagated into Polymarket implied probabilities.

This system was built to:

1. Identify mispricings using multiple independent mathematical signals
2. Require cross-agent consensus before committing capital (noise reduction)
3. Size positions using fractional Kelly criterion (bankroll-optimal, variance-managed)
4. Paper trade for 30+ days to validate edge before any live deployment

---

## Mathematical Models

This is the core intellectual contribution of the project. Each agent implements a distinct
quantitative model.

### 1. Shannon Entropy (Market Uncertainty Score)

Used by `MarketScannerAgent` to score which markets offer the most opportunity.

```
H(p) = -p * log2(p) - (1 - p) * log2(1 - p)
```

- H = 1.0 at p = 0.5 (maximum uncertainty, maximum opportunity)
- H approaches 0 near p = 0 or p = 1 (market has converged, no edge)
- Markets near resolution are excluded even if volume is high

### 2. Log-Odds Filter (Noise Rejection)

Applied by `ArbitrageAgent`, `BayesPriorAgent`, and `OTMOpportunityAgent` to discard
signals that are statistically indistinguishable from noise.

```
log_odds = ln(p / (1 - p))

|log_odds| < 0.5  =>  discard (probability between 37.8% and 62.2%)
|log_odds| >= 0.5 =>  signal is strong enough to act on
```

Rationale: small momentum moves produce probabilities near 50% where the log-odds are
compressed and the signal-to-noise ratio is too low to trade profitably after fees.

### 3. Momentum-to-Probability Mapping (ArbitrageAgent)

Converts Binance spot price momentum into an implied win probability for Polymarket
crypto contracts.

```
raw_p = 0.5 + (momentum_pct / 4.0) * 0.5
raw_p = clamp(raw_p, 0.05, 0.95)

# Then apply log-odds filter before trading
```

Example: BTC moves +1.0% in 60 seconds.
- raw_p = 0.5 + (1.0 / 4.0) * 0.5 = 0.625
- log_odds = ln(0.625 / 0.375) = 0.51 -- passes filter
- Polymarket 15-min BTC-up contract still shows 50% -- edge = 12.5% before fees

### 4. Bayesian Prior Mapping (BayesPriorAgent)

Implements a skeptical Bayesian update that penalises low-probability events before
accepting them as underpriced.

```
prior = p^2              (skeptical prior: always < p for p in (0, 1))
B     = estimated true probability from momentum signal
M     = B - p^2          (mapping function)

M > +0.06  =>  YES underpriced, buy YES
M < -0.06  =>  YES overpriced, buy NO
```

Example: market price p = 0.30, estimated true prob B = 0.50.

```
prior = 0.30^2 = 0.09
M     = 0.50 - 0.09 = +0.41   (strongly underpriced -> buy YES)
```

The p^2 squashing means the agent demands proportionally stronger evidence for
low-probability events - a built-in overconfidence correction.

### 5. Expected Value Ratio (OTMOpportunityAgent)

Targets out-of-the-money contracts priced 4%-10% where momentum suggests
the true probability is materially higher.

```
EV_ratio = estimated_true_prob / market_price

EV_ratio >= 1.8x  =>  trade
EV_ratio <  1.8x  =>  skip
```

Example: market prices YES at 5%, Binance momentum suggests true prob is 10%.
- EV_ratio = 0.10 / 0.05 = 2.0x -- exceeds threshold
- Edge after 1.56% taker fee = (0.10 - 0.05 - 0.0156) * 100 = 3.44%

The 1.8x threshold is calibrated to clear the taker fee and leave a positive
expected value with meaningful margin.

### 6. Quarter-Kelly Position Sizing (PositionSizerAgent)

Full Kelly criterion maximises long-run bankroll growth but is extremely sensitive
to edge estimation errors. This system uses quarter-Kelly for safety.

```
full_kelly    = edge_decimal * confidence
quarter_kelly = full_kelly * 0.25
bet_size      = bankroll * quarter_kelly

# Hard bounds: min $5, max 5% of bankroll
```

Example: edge = 15%, confidence = 0.70, bankroll = $1,000.

```
full_kelly    = 0.15 * 0.70 = 0.105  (10.5% of bankroll)
quarter_kelly = 0.105 * 0.25 = 0.026 (2.6% of bankroll)
bet_size      = $1,000 * 0.026 = $26
```

Quarter-Kelly gives the same growth direction as full Kelly at roughly one quarter
of the variance. With noisy edge estimates, this is the right tradeoff.

---

## Architecture

```
Data Sources (Layer 0)
  Polymarket Gamma API   ->  market discovery, volumes, questions
  Polymarket CLOB API    ->  live orderbook depth, bid/ask prices
  Binance REST/WebSocket ->  real-time BTC/ETH/SOL spot prices (no API key needed)
  NewsAPI                ->  headlines (free tier, 100 calls/day)

Specialist Agents (Layer 1) - each owns one signal type
  MarketScannerAgent     ->  entropy + liquidity scoring
  ArbitrageAgent         ->  Binance momentum vs Polymarket lag
  NewsAnalystAgent       ->  keyword-scored headline signals
  OTMOpportunityAgent    ->  EV ratio on underpriced OTM contracts
  BayesPriorAgent        ->  Bayesian p^2 prior mapping
  RiskManagerAgent       ->  drawdown and position limit enforcement

Orchestrator (Layer 2)
  MasterOrchestrator     ->  consensus logic (2+ agents must agree), routing

LLM Gate (Layer 3)
  Claude Opus 4.6        ->  final APPROVE/REJECT on every trade attempt

Paper Trading Engine (Layer 4)
  VirtualPortfolio       ->  $1,000 virtual USDC, exact fee + slippage simulation
  Trade Journal          ->  all decisions stored (executed, rejected, blocked)

Dashboard (Layer 5)
  Streamlit (Python)     ->  local PnL curve, signals, open positions
  Next.js + Supabase     ->  hosted dashboard with daily reports
```

---

## Trade Execution Pipeline

Every trade goes through four sequential gates. All four must pass.

```
1. CONSENSUS
   2+ agents agree on same market + same direction within a 5-minute window.
   Single-agent signals are logged but never executed.

2. RISK VALIDATION (RiskManagerAgent)
   - Max drawdown: halt all trading if drawdown >= 15%
   - Max open positions: reject if 3 already open
   - Max position size: cap at 5% of bankroll
   - Minimum viable size: reject if < $5

3. POSITION SIZING (PositionSizerAgent)
   Quarter-Kelly formula applied using average edge and confidence
   across all agreeing agents.

4. OPUS 4.6 LLM GATE
   Claude Opus 4.6 receives full trade context:
   market question, direction, size, edge estimate, agent reasoning.
   Returns APPROVE or REJECT only - no hedging.
   Any error or non-JSON response defaults to REJECT.
```

---

## Agents

### MarketScannerAgent

Scans all active markets every 60 seconds. Scores each by:

```
score = (liquidity_score * 0.5) + (shannon_entropy * 0.35) + lmsr_bonus

liquidity_score = min(volume / 100_000, 1.0)
lmsr_bonus      = 0.25 if yes_price < 0.07 else 0.0
```

Filters: volume > $10,000 and price between 5% and 95%.
Direction: YES if price < 0.5, NO if price > 0.5.

### ArbitrageAgent

Tracks BTC/ETH/SOL price history in memory (last 5 minutes).
Checks every 5 seconds via Binance REST (no API key required).

```python
momentum_pct = (new_price - old_price) / old_price * 100
true_prob    = momentum_to_probability(momentum_pct)  # log-odds filtered
edge         = true_prob - market_price - 0.0156      # subtract taker fee
```

Signals only when edge > 5% after fees.

### OTMOpportunityAgent

Targets YES prices in the 4%-10% range.
Uses 5-minute Binance momentum to estimate true probability.
Signals when EV ratio >= 1.8x after fees.

### BayesPriorAgent

Targets crypto and crypto-correlated stock markets (BTC, ETH, SOL, MARA, MSTR).
Uses 5-minute momentum and the p^2 skeptical prior.
Signals when |M| > 0.06 and edge survives the 1.56% fee.

### NewsAnalystAgent

Fetches headlines from NewsAPI. Scores by keyword matching (works without AI).
Optionally upgrades to Claude Haiku analysis when `ANTHROPIC_API_KEY` is set.
Signals only when score >= 7/10 and confidence >= 60%.

### RiskManagerAgent

Passive gatekeeper - does not generate trade signals.
Monitors drawdown and position count continuously.
Emits a HALT signal if the 15% drawdown limit is breached.

---

## Fee Simulation

Every paper trade applies exact real-world costs so performance data is accurate.

```
Taker order (market buy):  1.56% of trade size
Maker order (limit order): 0.00% (earns rebate in reality)
Gas per transaction:       $0.02 (Polygon network)

Slippage (order < $50):    +0.1%
Slippage ($50 - $200):     +0.5%
Slippage ($200 - $1,000):  +1.0%
Slippage (> $1,000):       +2.0%
With real orderbook:       walks book levels for exact average fill price
```

---

## File Structure

```
polymarket-trading/
|
+-- run.py                    Entry point. Starts all agents and orchestrator.
+-- requirements.txt          All Python dependencies.
+-- .env.example              Config template. Copy to .env before running.
|
+-- core/
|   +-- orchestrator.py       MasterOrchestrator: consensus, Opus gate, routing.
|   +-- portfolio.py          VirtualPortfolio: fee sim, slippage, PnL tracking.
|   +-- database.py           SQLite schema and all read/write functions.
|   +-- supabase_db.py        Supabase backend (auto-selected when SUPABASE_URL set).
|   +-- logger.py             Shared rotating logger.
|
+-- agents/
|   +-- agents.py             All six agents + BaseAgent contract.
|
+-- data/
|   +-- polymarket_fetcher.py Gamma API + CLOB API + WebSocket stream.
|   +-- binance_fetcher.py    BTC/ETH/SOL price feed (no API key needed).
|   +-- news_fetcher.py       NewsAPI + keyword scoring + optional Haiku analysis.
|
+-- research/
|   +-- strategy_researcher.py Autonomous parameter optimizer (Opus-assisted).
|
+-- reports/
|   +-- generate_report.py    End-of-day markdown report generator.
|
+-- dashboard/
|   +-- app.py                Streamlit dashboard (local).
|
+-- webapp/                   Next.js hosted dashboard (Vercel + Supabase).
|
+-- tests/
|   +-- test_all.py           8 component tests. No network required.
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env - add NEWS_API_KEY at minimum (free at newsapi.org)

# 3. Verify everything works
python tests/test_all.py
# All 8 tests should pass

# 4. Start the bot (Terminal 1)
python run.py

# 5. Open dashboard (Terminal 2)
streamlit run dashboard/app.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEWS_API_KEY` | Recommended | - | Free at newsapi.org. 100 requests/day. Without it, bot uses mock headlines. |
| `ANTHROPIC_API_KEY` | Optional | - | Enables Claude Opus gate + Haiku news analysis. Without it, Opus gate is bypassed. |
| `SUPABASE_URL` | Optional | - | Enables Supabase backend instead of SQLite. |
| `SUPABASE_KEY` | Optional | - | Supabase anon key. Required when SUPABASE_URL is set. |
| `STARTING_BANKROLL` | No | 1000.0 | Virtual USDC starting balance. |
| `MAX_POSITION_PCT` | No | 0.05 | Max single position as % of bankroll. |
| `MAX_OPEN_POSITIONS` | No | 3 | Max concurrent open trades. |
| `MAX_DRAWDOWN_PCT` | No | 0.15 | Halt trading if drawdown exceeds this. |
| `MARKET_SCAN_INTERVAL` | No | 60 | Seconds between market scans. |
| `NEWS_POLL_INTERVAL` | No | 60 | Seconds between news fetches. |

---

## Database Schema

SQLite by default (`data/polymarket_bot.db`). Switches to Supabase when `SUPABASE_URL` is set.

| Table | What it stores |
|---|---|
| `markets` | Active Polymarket markets with prices and volumes |
| `price_snapshots` | Orderbook snapshots (bid/ask/spread) per market per tick |
| `spot_prices` | Binance BTC/ETH/SOL price history |
| `agent_signals` | Every signal from every agent with full context |
| `paper_trades` | Full trade record: entry, exit, fees, PnL, agent source |
| `portfolio_snapshots` | Bankroll value over time (for PnL curve) |
| `news_log` | All headlines with relevance score and direction signal |
| `trade_journal` | Full decision context: executed, Opus-rejected, and risk-blocked trades |

Useful queries after running the bot:

```sql
-- Which agent combos are most profitable?
SELECT agent_source, COUNT(*) trades, SUM(pnl) total_pnl, AVG(pnl) avg_pnl
FROM paper_trades WHERE status = 'closed'
GROUP BY agent_source ORDER BY total_pnl DESC;

-- Win rate by direction
SELECT direction, COUNT(*) trades,
       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins
FROM paper_trades WHERE status = 'closed'
GROUP BY direction;

-- Fee drag vs gross PnL
SELECT SUM(taker_fee + gas_cost) total_fees,
       SUM(pnl) gross_pnl,
       SUM(taker_fee + gas_cost) / ABS(SUM(pnl)) fee_ratio
FROM paper_trades WHERE status = 'closed';
```

---

## Adding a New Agent

```python
# agents/agents.py
class MyNewAgent(BaseAgent):
    def __init__(self):
        super().__init__("MyNewAgent")

    def get_signal(self) -> dict:
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

Then in `core/orchestrator.py`:

```python
# __init__
self.my_agent = MyNewAgent()

# start() - add a thread
Thread(target=self._run_my_agent, daemon=True, name="MyAgent").start()
```

---

## Running Tests

```bash
python tests/test_all.py
```

Covers: database init, portfolio open/close, market scanner, arbitrage momentum,
news keyword scoring, risk validation, Kelly sizing, and full end-to-end trade flow.
All tests use mock data - no real API calls, no network required.

---

## Upgrading to Live Trading

Replace `VirtualPortfolio.open_position()` with a real CLOB order:

```python
from py_clob_client.client import ClobClient
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=137)
# place real limit or market order here
```

**Only do this after 30+ days of profitable paper trading with stable edge estimates.**

---

## Design Decisions

**Why quarter-Kelly?** Full Kelly maximises long-run growth only when edge estimates are
perfect. Since our edge comes from noisy momentum signals, quarter-Kelly provides a 75%
safety margin. It is the standard choice in systematic trading before live calibration.

**Why consensus from 2 agents?** Single-agent signals have high false-positive rates on
prediction markets. Requiring two independent mathematical signals to agree on the same
market substantially reduces noise trades. The 5-minute consensus window is wide enough
to catch agents running on different scan intervals.

**Why Opus 4.6 as the gate?** The LLM gate is not a quant model - it is a sanity checker.
It reads the agent reasoning and rejects trades where the logic is internally inconsistent
(e.g. direction and stated edge do not match). It adds a qualitative filter that pure
quantitative rules cannot easily express.

**Why threads not asyncio?** Each agent runs in its own thread with isolated error handling.
One agent crashing does not affect others. The signal queue is the only shared state,
which keeps the concurrency model simple and auditable.

**Why SQLite first?** Zero setup, fully portable, directly queryable. The schema is
identical to the Supabase schema so migration is a single env var change.

---

## Warnings

- This is a paper trading simulator. It does not place real trades.
- Polymarket is geo-restricted. Check your local regulations before live trading.
- Past paper trading performance does not guarantee live trading performance.
- The arbitrage window on Polymarket has compressed since 2024. Edge estimates from
  ArbitrageAgent are approximate and must be validated against at least 30 days of data.
- Never trade more than you can afford to lose.
