"""
core/supabase_db.py

Supabase-backed database layer. Drop-in replacement for database.py.
All functions match the same signatures so nothing else needs to change.

Set in .env:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=eyJ...
"""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_client = None


def get_client():
    global _client
    if _client is None:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
            )
        _client = create_client(url, key)
    return _client


def init_database():
    """No-op - schema is created via supabase_schema.sql in the dashboard."""
    logger.info("Supabase DB layer initialised (schema managed via supabase_schema.sql)")


# ── Markets ──────────────────────────────────────────────────────────────────

def save_markets(markets: list):
    if not markets:
        return
    rows = []
    for m in markets:
        clob = m.get("clobTokenIds", [])
        rows.append({
            "condition_id":   m.get("condition_id", m.get("id", "")),
            "question":       m.get("question", ""),
            "category":       m.get("category", ""),
            "end_date":       m.get("endDate", ""),
            "token_id_yes":   clob[0] if clob else "",
            "token_id_no":    clob[1] if len(clob) > 1 else "",
            "volume":         float(m.get("volume", 0) or 0),
            "last_price_yes": float((m.get("outcomePrices") or [0])[0] or 0),
            "last_price_no":  float((m.get("outcomePrices") or [0, 0])[1] or 0)
                              if len(m.get("outcomePrices") or []) > 1 else 0,
            "is_active":      1,
            "fetched_at":     datetime.utcnow().isoformat(),
        })
    get_client().table("markets").upsert(rows, on_conflict="condition_id").execute()


def get_active_markets(limit: int = 50) -> list:
    result = (
        get_client().table("markets")
        .select("*")
        .eq("is_active", 1)
        .order("volume", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Prices ───────────────────────────────────────────────────────────────────

def save_price_snapshot(condition_id: str, side: str, bid: float, ask: float):
    mid    = (bid + ask) / 2 if bid and ask else 0
    spread = ask - bid if bid and ask else 0
    get_client().table("price_snapshots").insert({
        "condition_id": condition_id,
        "side":         side,
        "best_bid":     bid,
        "best_ask":     ask,
        "mid_price":    mid,
        "spread":       spread,
        "timestamp":    datetime.utcnow().isoformat(),
    }).execute()


def save_spot_price(symbol: str, price: float):
    get_client().table("spot_prices").insert({
        "symbol":    symbol,
        "price":     price,
        "timestamp": datetime.utcnow().isoformat(),
    }).execute()


def get_latest_spot(symbol: str) -> float:
    result = (
        get_client().table("spot_prices")
        .select("price")
        .eq("symbol", symbol)
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0]["price"] if result.data else None


# ── Signals ───────────────────────────────────────────────────────────────────

def save_signal(agent_name: str, condition_id: str, signal_type: str,
                direction: str = None, confidence: float = 0,
                edge_pct: float = 0, raw_data: dict = None):
    get_client().table("agent_signals").insert({
        "agent_name":    agent_name,
        "condition_id":  condition_id,
        "signal_type":   signal_type,
        "direction":     direction,
        "confidence":    confidence,
        "edge_pct":      edge_pct,
        "raw_data":      raw_data or {},
        "timestamp":     datetime.utcnow().isoformat(),
    }).execute()


def get_recent_signals(limit: int = 20) -> list:
    result = (
        get_client().table("agent_signals")
        .select("*")
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Paper Trades ──────────────────────────────────────────────────────────────

def save_trade(trade: dict) -> int:
    """Insert a new paper trade. Returns the DB id."""
    row = {
        "condition_id": trade["condition_id"],
        "question":     trade.get("question", "")[:200],
        "direction":    trade["direction"],
        "order_type":   trade["order_type"],
        "size_usdc":    trade["size_usdc"],
        "entry_price":  trade["entry_price"],
        "fill_price":   trade["fill_price"],
        "slippage":     trade.get("slippage", 0),
        "taker_fee":    trade.get("taker_fee", 0),
        "gas_cost":     trade.get("gas_cost", 0),
        "total_cost":   trade["total_cost"],
        "contracts":    trade["contracts"],
        "agent_source": trade.get("agent_source", ""),
        "status":       "open",
        "opened_at":    trade.get("opened_at", datetime.utcnow().isoformat()),
    }
    result = get_client().table("paper_trades").insert(row).execute()
    return result.data[0]["id"] if result.data else None


def update_trade(trade: dict):
    """Update a closed trade record."""
    (
        get_client().table("paper_trades")
        .update({
            "status":     trade["status"],
            "exit_price": trade.get("exit_price"),
            "pnl":        trade.get("pnl"),
            "closed_at":  trade.get("closed_at"),
        })
        .eq("condition_id", trade["condition_id"])
        .eq("opened_at", trade["opened_at"])
        .execute()
    )


def get_open_trades() -> list:
    result = (
        get_client().table("paper_trades")
        .select("*")
        .eq("status", "open")
        .order("opened_at", desc=True)
        .execute()
    )
    return result.data or []


def get_trade_history(limit: int = 100) -> list:
    result = (
        get_client().table("paper_trades")
        .select("*")
        .order("opened_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Portfolio Snapshots ───────────────────────────────────────────────────────

def save_portfolio_snapshot(bankroll: float, open_positions: int,
                             total_pnl: float, win_count: int, loss_count: int):
    get_client().table("portfolio_snapshots").insert({
        "bankroll":       bankroll,
        "open_positions": open_positions,
        "total_pnl":      total_pnl,
        "win_count":      win_count,
        "loss_count":     loss_count,
        "timestamp":      datetime.utcnow().isoformat(),
    }).execute()


def get_portfolio_history() -> list:
    result = (
        get_client().table("portfolio_snapshots")
        .select("*")
        .order("timestamp", desc=True)
        .limit(2000)
        .execute()
    )
    return list(reversed(result.data or []))


# ── News ──────────────────────────────────────────────────────────────────────

def save_news(headline: str, source: str, url: str,
              relevance_score: int, keywords_found: list,
              ai_signal: str = None, ai_confidence: float = None):
    get_client().table("news_log").insert({
        "headline":        headline,
        "source":          source,
        "url":             url,
        "relevance_score": relevance_score,
        "keywords_found":  ",".join(keywords_found),
        "ai_signal":       ai_signal,
        "ai_confidence":   ai_confidence,
        "timestamp":       datetime.utcnow().isoformat(),
    }).execute()


# ── Trade Journal ─────────────────────────────────────────────────────────────

def save_trade_journal(condition_id: str, question: str, direction: str,
                       proposed_size: float, entry_price: float,
                       agent_sources: str, agent_signals: str,
                       opus_verdict: str, opus_reasoning: str,
                       outcome: str, avg_edge: float, avg_confidence: float,
                       market_volume: float = 0):
    try:
        signals_data = json.loads(agent_signals) if isinstance(agent_signals, str) else agent_signals
    except Exception:
        signals_data = []

    get_client().table("trade_journal").insert({
        "condition_id":   condition_id,
        "question":       (question or "")[:300],
        "direction":      direction,
        "proposed_size":  proposed_size,
        "entry_price":    entry_price,
        "agent_sources":  agent_sources,
        "agent_signals":  signals_data,
        "opus_verdict":   opus_verdict,
        "opus_reasoning": (opus_reasoning or "")[:500],
        "outcome":        outcome,
        "avg_edge":       avg_edge,
        "avg_confidence": avg_confidence,
        "market_volume":  market_volume,
        "logged_at":      datetime.utcnow().isoformat(),
    }).execute()


def get_trade_journal(date_str: str = None, limit: int = 500) -> list:
    q = get_client().table("trade_journal").select("*").order("logged_at", desc=True)
    if date_str:
        q = q.gte("logged_at", f"{date_str}T00:00:00").lte("logged_at", f"{date_str}T23:59:59")
    result = q.limit(limit).execute()
    return result.data or []


# ── Strategy Params ───────────────────────────────────────────────────────────

def get_strategy_params() -> dict:
    """Load all strategy params as a key→value dict."""
    result = get_client().table("strategy_params").select("param_key,param_value").execute()
    return {row["param_key"]: row["param_value"] for row in (result.data or [])}


def save_strategy_param(key: str, value: float, previous: float,
                        reason: str, win_rate: float = None, pnl: float = None):
    get_client().table("strategy_params").upsert({
        "param_key":          key,
        "param_value":        value,
        "previous_value":     previous,
        "reason":             reason,
        "backtest_win_rate":  win_rate,
        "backtest_pnl":       pnl,
        "updated_at":         datetime.utcnow().isoformat(),
    }, on_conflict="param_key").execute()
