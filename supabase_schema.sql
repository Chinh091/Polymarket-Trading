-- ============================================================
-- Polymarket Paper Trading Bot — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Markets
CREATE TABLE IF NOT EXISTS markets (
    condition_id    TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    category        TEXT,
    end_date        TEXT,
    token_id_yes    TEXT,
    token_id_no     TEXT,
    volume          REAL DEFAULT 0,
    last_price_yes  REAL,
    last_price_no   REAL,
    is_active       INTEGER DEFAULT 1,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Price snapshots
CREATE TABLE IF NOT EXISTS price_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    condition_id    TEXT NOT NULL,
    side            TEXT NOT NULL,
    best_bid        REAL,
    best_ask        REAL,
    mid_price       REAL,
    spread          REAL,
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Spot prices (Binance)
CREATE TABLE IF NOT EXISTS spot_prices (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    price           REAL NOT NULL,
    source          TEXT DEFAULT 'binance',
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Agent signals
CREATE TABLE IF NOT EXISTS agent_signals (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    condition_id    TEXT,
    signal_type     TEXT NOT NULL,
    direction       TEXT,
    confidence      REAL,
    edge_pct        REAL,
    raw_data        JSONB,
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Paper trades
CREATE TABLE IF NOT EXISTS paper_trades (
    id              BIGSERIAL PRIMARY KEY,
    condition_id    TEXT NOT NULL,
    question        TEXT,
    direction       TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    size_usdc       REAL NOT NULL,
    entry_price     REAL NOT NULL,
    fill_price      REAL NOT NULL,
    slippage        REAL DEFAULT 0,
    taker_fee       REAL DEFAULT 0,
    gas_cost        REAL DEFAULT 0,
    total_cost      REAL NOT NULL,
    contracts       REAL NOT NULL,
    agent_source    TEXT,
    status          TEXT DEFAULT 'open',
    exit_price      REAL,
    pnl             REAL,
    closed_at       TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    bankroll        REAL NOT NULL,
    open_positions  INTEGER DEFAULT 0,
    total_pnl       REAL DEFAULT 0,
    win_count       INTEGER DEFAULT 0,
    loss_count      INTEGER DEFAULT 0,
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- News log
CREATE TABLE IF NOT EXISTS news_log (
    id              BIGSERIAL PRIMARY KEY,
    headline        TEXT NOT NULL,
    source          TEXT,
    url             TEXT,
    relevance_score INTEGER DEFAULT 0,
    keywords_found  TEXT,
    ai_signal       TEXT,
    ai_confidence   REAL,
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Trade journal (full decision context)
CREATE TABLE IF NOT EXISTS trade_journal (
    id              BIGSERIAL PRIMARY KEY,
    condition_id    TEXT NOT NULL,
    question        TEXT,
    direction       TEXT,
    proposed_size   REAL,
    entry_price     REAL,
    agent_sources   TEXT,
    agent_signals   JSONB,
    opus_verdict    TEXT,
    opus_reasoning  TEXT,
    outcome         TEXT,
    avg_edge        REAL,
    avg_confidence  REAL,
    market_volume   REAL,
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Strategy params (autoresearcher writes here, bot reads on startup)
CREATE TABLE IF NOT EXISTS strategy_params (
    id              BIGSERIAL PRIMARY KEY,
    param_key       TEXT UNIQUE NOT NULL,
    param_value     REAL NOT NULL,
    previous_value  REAL,
    reason          TEXT,
    backtest_win_rate   REAL,
    backtest_pnl        REAL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default strategy params
INSERT INTO strategy_params (param_key, param_value, reason) VALUES
    ('min_edge_arb',          5.0,   'ArbitrageAgent minimum edge % after fees'),
    ('log_odds_threshold',    0.5,   'Log-odds filter: |log-odds| must exceed this'),
    ('kelly_fraction',        0.25,  'Quarter-Kelly position sizing multiplier'),
    ('min_consensus',         2.0,   'Minimum agents that must agree to trade'),
    ('consensus_window',      300.0, 'Seconds within which agents must agree'),
    ('max_position_pct',      0.05,  'Max single position as fraction of bankroll'),
    ('otm_min_ev',            1.8,   'OTMOpportunityAgent minimum EV ratio'),
    ('bayes_m_threshold',     0.06,  'BayesPriorAgent mapping function threshold'),
    ('market_scan_interval',  60.0,  'Seconds between market scans'),
    ('news_poll_interval',    60.0,  'Seconds between news fetches')
ON CONFLICT (param_key) DO NOTHING;

-- Enable Row Level Security (RLS) — anon key can read, service role can write
ALTER TABLE markets            ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_trades       ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_signals      ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_journal      ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE spot_prices        ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_params    ENABLE ROW LEVEL SECURITY;

-- Allow public read access (for the web dashboard anon key)
CREATE POLICY "public read markets"            ON markets            FOR SELECT USING (true);
CREATE POLICY "public read paper_trades"       ON paper_trades       FOR SELECT USING (true);
CREATE POLICY "public read agent_signals"      ON agent_signals      FOR SELECT USING (true);
CREATE POLICY "public read trade_journal"      ON trade_journal      FOR SELECT USING (true);
CREATE POLICY "public read portfolio_snapshots" ON portfolio_snapshots FOR SELECT USING (true);
CREATE POLICY "public read news_log"           ON news_log           FOR SELECT USING (true);
CREATE POLICY "public read spot_prices"        ON spot_prices        FOR SELECT USING (true);
CREATE POLICY "public read strategy_params"    ON strategy_params    FOR SELECT USING (true);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_markets_volume       ON markets (volume DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status        ON paper_trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_opened        ON paper_trades (opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp    ON agent_signals (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_journal_logged       ON trade_journal (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp  ON portfolio_snapshots (timestamp ASC);
CREATE INDEX IF NOT EXISTS idx_spot_symbol          ON spot_prices (symbol, timestamp DESC);
