"""
core/database.py
Handles all SQLite storage for markets, prices, trades, and signals.
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "polymarket_bot.db"

logger = logging.getLogger(__name__)


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Markets table — stores active Polymarket markets
    c.execute("""
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
            fetched_at      TEXT NOT NULL
        )
    """)

    # Price snapshots — real-time orderbook data
    c.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id    TEXT NOT NULL,
            side            TEXT NOT NULL,
            best_bid        REAL,
            best_ask        REAL,
            mid_price       REAL,
            spread          REAL,
            timestamp       TEXT NOT NULL
        )
    """)

    # Spot prices — Binance BTC/ETH/SOL
    c.execute("""
        CREATE TABLE IF NOT EXISTS spot_prices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            price           REAL NOT NULL,
            source          TEXT DEFAULT 'binance',
            timestamp       TEXT NOT NULL
        )
    """)

    # Agent signals — every signal from every agent
    c.execute("""
        CREATE TABLE IF NOT EXISTS agent_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name      TEXT NOT NULL,
            condition_id    TEXT,
            signal_type     TEXT NOT NULL,
            direction       TEXT,
            confidence      REAL,
            edge_pct        REAL,
            raw_data        TEXT,
            timestamp       TEXT NOT NULL
        )
    """)

    # Paper trades — every simulated trade
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
            contracts        REAL NOT NULL,
            agent_source    TEXT,
            status          TEXT DEFAULT 'open',
            exit_price      REAL,
            pnl             REAL,
            closed_at       TEXT,
            opened_at       TEXT NOT NULL
        )
    """)

    # Portfolio snapshots — bankroll over time
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bankroll        REAL NOT NULL,
            open_positions  INTEGER DEFAULT 0,
            total_pnl       REAL DEFAULT 0,
            win_count       INTEGER DEFAULT 0,
            loss_count      INTEGER DEFAULT 0,
            timestamp       TEXT NOT NULL
        )
    """)

    # News log — headlines + keyword scores (no API needed)
    c.execute("""
        CREATE TABLE IF NOT EXISTS news_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            headline        TEXT NOT NULL,
            source          TEXT,
            url             TEXT,
            relevance_score INTEGER DEFAULT 0,
            keywords_found  TEXT,
            ai_signal       TEXT,
            ai_confidence   REAL,
            timestamp       TEXT NOT NULL
        )
    """)

    # Trade journal — full context for every decision (approved AND rejected)
    # This is the primary source for end-of-day reports and future improvement
    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id    TEXT NOT NULL,
            question        TEXT,
            direction       TEXT,
            proposed_size   REAL,
            entry_price     REAL,
            agent_sources   TEXT,
            agent_signals   TEXT,   -- JSON: full signal from each agent
            opus_verdict    TEXT,   -- APPROVE | REJECT
            opus_reasoning  TEXT,
            outcome         TEXT,   -- 'executed' | 'rejected_opus' | 'rejected_risk' | 'zero_size'
            avg_edge        REAL,
            avg_confidence  REAL,
            market_volume   REAL,
            logged_at       TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    logger.info(f"Database initialised at {DB_PATH}")


def save_markets(markets: list):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for m in markets:
        c.execute("""
            INSERT OR REPLACE INTO markets
            (condition_id, question, category, end_date,
             token_id_yes, token_id_no, volume, last_price_yes,
             last_price_no, is_active, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            m.get("condition_id", m.get("id","")),
            m.get("question", ""),
            m.get("category", ""),
            m.get("endDate", ""),
            m.get("clobTokenIds", ["",""])[0] if m.get("clobTokenIds") else "",
            m.get("clobTokenIds", ["",""])[1] if m.get("clobTokenIds") and len(m.get("clobTokenIds",[])) > 1 else "",
            float(m.get("volume", 0) or 0),
            float(m.get("outcomePrices", [0,0])[0] or 0) if m.get("outcomePrices") else 0,
            float(m.get("outcomePrices", [0,0])[1] or 0) if m.get("outcomePrices") and len(m.get("outcomePrices",[])) > 1 else 0,
            1,
            now
        ))
    conn.commit()
    conn.close()


def save_price_snapshot(condition_id: str, side: str, bid: float, ask: float):
    conn = get_connection()
    mid = (bid + ask) / 2 if bid and ask else 0
    spread = ask - bid if bid and ask else 0
    conn.execute("""
        INSERT INTO price_snapshots
        (condition_id, side, best_bid, best_ask, mid_price, spread, timestamp)
        VALUES (?,?,?,?,?,?,?)
    """, (condition_id, side, bid, ask, mid, spread, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def save_spot_price(symbol: str, price: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO spot_prices (symbol, price, timestamp)
        VALUES (?,?,?)
    """, (symbol, price, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def save_signal(agent_name: str, condition_id: str, signal_type: str,
                direction: str = None, confidence: float = 0,
                edge_pct: float = 0, raw_data: dict = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO agent_signals
        (agent_name, condition_id, signal_type, direction,
         confidence, edge_pct, raw_data, timestamp)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        agent_name, condition_id, signal_type, direction,
        confidence, edge_pct,
        json.dumps(raw_data) if raw_data else None,
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def save_news(headline: str, source: str, url: str,
              relevance_score: int, keywords_found: list,
              ai_signal: str = None, ai_confidence: float = None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO news_log
        (headline, source, url, relevance_score, keywords_found,
         ai_signal, ai_confidence, timestamp)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        headline, source, url, relevance_score,
        ",".join(keywords_found),
        ai_signal, ai_confidence,
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def get_active_markets(limit: int = 50) -> list:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM markets
        WHERE is_active = 1
        ORDER BY volume DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_spot(symbol: str) -> float:
    conn = get_connection()
    row = conn.execute("""
        SELECT price FROM spot_prices
        WHERE symbol = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (symbol,)).fetchone()
    conn.close()
    return row["price"] if row else None


def get_recent_signals(limit: int = 20) -> list:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM agent_signals
        ORDER BY timestamp DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_trades() -> list:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM paper_trades WHERE status = 'open'
        ORDER BY opened_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(limit: int = 100) -> list:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM paper_trades
        ORDER BY opened_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_trade_journal(condition_id: str, question: str, direction: str,
                       proposed_size: float, entry_price: float,
                       agent_sources: str, agent_signals: str,
                       opus_verdict: str, opus_reasoning: str,
                       outcome: str, avg_edge: float, avg_confidence: float,
                       market_volume: float = 0):
    conn = get_connection()
    conn.execute("""
        INSERT INTO trade_journal
        (condition_id, question, direction, proposed_size, entry_price,
         agent_sources, agent_signals, opus_verdict, opus_reasoning,
         outcome, avg_edge, avg_confidence, market_volume, logged_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        condition_id, question, direction, proposed_size, entry_price,
        agent_sources, agent_signals, opus_verdict, opus_reasoning,
        outcome, avg_edge, avg_confidence, market_volume,
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def get_trade_journal(date_str: str = None, limit: int = 500) -> list:
    """Fetch journal entries. date_str = 'YYYY-MM-DD' to filter by day."""
    conn = get_connection()
    if date_str:
        rows = conn.execute("""
            SELECT * FROM trade_journal
            WHERE logged_at LIKE ?
            ORDER BY logged_at DESC LIMIT ?
        """, (f"{date_str}%", limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM trade_journal
            ORDER BY logged_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio_history() -> list:
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM portfolio_snapshots
        ORDER BY timestamp ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
