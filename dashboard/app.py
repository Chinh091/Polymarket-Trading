"""
dashboard/app.py
Streamlit dashboard — run with: streamlit run dashboard/app.py
Shows live portfolio performance, signals, and trade history.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.database import (
    get_portfolio_history, get_trade_history,
    get_recent_signals, get_open_trades, get_active_markets
)

st.set_page_config(
    page_title="Polymarket Paper Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.title("📈 Polymarket Paper Trading Bot")
st.caption("Simulation only — no real money. Refresh every 30s for live data.")

# Auto-refresh every 30 seconds
st.markdown("""
<script>
setTimeout(function(){window.location.reload()}, 30000);
</script>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------
# Portfolio Stats (top row)
# ------------------------------------------------------------------

portfolio = get_portfolio_history()
trades    = get_trade_history(limit=500)
open_pos  = get_open_trades()

# Calculate stats
if portfolio:
    latest = portfolio[-1]
    bankroll   = latest.get("bankroll", 1000)
    start      = portfolio[0].get("bankroll", 1000)
    total_pnl  = bankroll - start
    pnl_pct    = (total_pnl / start * 100) if start > 0 else 0
    peak       = max(p["bankroll"] for p in portfolio)
    drawdown   = (peak - bankroll) / peak * 100 if peak > 0 else 0
else:
    bankroll, total_pnl, pnl_pct, drawdown = 1000, 0, 0, 0

closed_trades = [t for t in trades if t.get("status") == "closed"]
wins   = [t for t in closed_trades if (t.get("pnl") or 0) > 0]
losses = [t for t in closed_trades if (t.get("pnl") or 0) <= 0]
win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
total_fees = sum((t.get("taker_fee",0) or 0) + (t.get("gas_cost",0) or 0) for t in closed_trades)

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("💰 Bankroll",    f"${bankroll:,.2f}")
col2.metric("📊 Total PnL",   f"${total_pnl:+,.2f}", f"{pnl_pct:+.1f}%")
col3.metric("📉 Drawdown",    f"{drawdown:.1f}%")
col4.metric("🏆 Win Rate",    f"{win_rate:.1f}%",    f"{len(wins)}W / {len(losses)}L")
col5.metric("📂 Open Pos",    len(open_pos))
col6.metric("💸 Fees Paid",   f"${total_fees:.3f}")

st.divider()

# ------------------------------------------------------------------
# PnL Curve
# ------------------------------------------------------------------

col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("Portfolio Value Over Time")
    if portfolio:
        df_port = pd.DataFrame(portfolio)
        df_port["timestamp"] = pd.to_datetime(df_port["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_port["timestamp"],
            y=df_port["bankroll"],
            mode="lines",
            name="Bankroll",
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,212,170,0.1)"
        ))
        fig.add_hline(
            y=df_port["bankroll"].iloc[0],
            line_dash="dash",
            line_color="gray",
            annotation_text="Start"
        )
        fig.update_layout(
            height=300,
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis_title=None,
            yaxis_title="USDC",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No portfolio data yet — bot hasn't made any trades.")

with col_right:
    st.subheader("Agent Attribution")
    if closed_trades:
        agent_pnl = {}
        for t in closed_trades:
            agent = t.get("agent_source", "unknown") or "unknown"
            pnl   = t.get("pnl", 0) or 0
            agent_pnl[agent] = agent_pnl.get(agent, 0) + pnl
        df_agents = pd.DataFrame([
            {"Agent": k, "PnL": v} for k, v in agent_pnl.items()
        ])
        colors = ["#00d4aa" if v >= 0 else "#ff6b6b" for v in df_agents["PnL"]]
        fig2 = px.bar(df_agents, x="Agent", y="PnL",
                      color_discrete_sequence=colors)
        fig2.update_layout(
            height=300, margin=dict(l=0, r=0, t=0, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)"
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No closed trades yet.")

st.divider()

# ------------------------------------------------------------------
# Open Positions
# ------------------------------------------------------------------

st.subheader(f"Open Positions ({len(open_pos)})")
if open_pos:
    df_open = pd.DataFrame(open_pos)
    cols_show = ["condition_id", "direction", "size_usdc", "fill_price",
                 "contracts", "taker_fee", "gas_cost", "agent_source", "opened_at"]
    cols_show = [c for c in cols_show if c in df_open.columns]
    st.dataframe(df_open[cols_show], use_container_width=True, height=200)
else:
    st.info("No open positions.")

# ------------------------------------------------------------------
# Recent Agent Signals
# ------------------------------------------------------------------

st.subheader("Recent Agent Signals (last 20)")
signals = get_recent_signals(limit=20)
if signals:
    df_sig = pd.DataFrame(signals)
    def colour_signal(val):
        if val == "TRADE":   return "background-color: #1a4a1a"
        if val == "HALT":    return "background-color: #4a1a1a"
        return ""
    cols_sig = ["timestamp", "agent_name", "signal_type", "direction",
                "confidence", "edge_pct", "condition_id"]
    cols_sig = [c for c in cols_sig if c in df_sig.columns]
    st.dataframe(df_sig[cols_sig], use_container_width=True, height=250)
else:
    st.info("No signals yet — agents are starting up.")

# ------------------------------------------------------------------
# Trade History
# ------------------------------------------------------------------

st.subheader("Trade History (last 50)")
if closed_trades:
    df_trades = pd.DataFrame(closed_trades[-50:])
    if "pnl" in df_trades.columns:
        df_trades["result"] = df_trades["pnl"].apply(
            lambda x: "✅ WIN" if (x or 0) > 0 else "❌ LOSS"
        )
    cols_trade = ["opened_at", "question", "direction", "size_usdc",
                  "fill_price", "exit_price", "taker_fee", "pnl", "result",
                  "agent_source"]
    cols_trade = [c for c in cols_trade if c in df_trades.columns]
    st.dataframe(df_trades[cols_trade], use_container_width=True, height=300)
else:
    st.info("No closed trades yet.")

# ------------------------------------------------------------------
# Active Markets
# ------------------------------------------------------------------

with st.expander("📋 Active Markets in Database"):
    markets = get_active_markets(limit=20)
    if markets:
        df_mkt = pd.DataFrame(markets)
        cols_mkt = ["question", "volume", "last_price_yes", "category", "fetched_at"]
        cols_mkt = [c for c in cols_mkt if c in df_mkt.columns]
        st.dataframe(df_mkt[cols_mkt], use_container_width=True)
    else:
        st.info("No markets fetched yet.")

st.caption(f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC | Paper trading only — not financial advice")
