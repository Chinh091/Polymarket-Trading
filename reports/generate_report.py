"""
reports/generate_report.py

Generates a full end-of-day trading report from the SQLite database.
Covers every decision made: executed trades, Opus rejections, risk blocks.
Saves a markdown file to reports/report_YYYYMMDD.md

Usage:
    python reports/generate_report.py            # today
    python reports/generate_report.py 2026-03-22 # specific date
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, date

# Make sure imports from parent work
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

# Auto-select Supabase when configured
if os.getenv("SUPABASE_URL"):
    from core.supabase_db import (
        get_trade_journal, get_trade_history,
        get_portfolio_history, get_recent_signals
    )
else:
    from core.database import (
        get_trade_journal, get_trade_history,
        get_portfolio_history, get_recent_signals
    )

REPORTS_DIR = Path(__file__).parent


def _pct(val):
    return f"{val * 100:.1f}%" if val is not None else "—"


def _usd(val):
    return f"${val:+.2f}" if val is not None else "—"


def _safe(val, fmt=".2f"):
    return format(val, fmt) if val is not None else "—"


def generate(target_date: str = None) -> str:
    if target_date is None:
        target_date = date.today().isoformat()

    journal   = get_trade_journal(date_str=target_date)
    all_trades = get_trade_history(limit=500)
    portfolio  = get_portfolio_history()

    # Filter today's closed trades
    today_trades = [
        t for t in all_trades
        if t.get("opened_at", "").startswith(target_date)
        or t.get("closed_at", "").startswith(target_date)
    ]
    closed = [t for t in today_trades if t.get("status") == "closed"]
    open_  = [t for t in today_trades if t.get("status") == "open"]

    # Portfolio stats
    if portfolio:
        start_bank = portfolio[0]["bankroll"]
        end_bank   = portfolio[-1]["bankroll"]
        today_snaps = [p for p in portfolio if p["timestamp"].startswith(target_date)]
        day_start  = today_snaps[0]["bankroll"] if today_snaps else start_bank
        day_end    = today_snaps[-1]["bankroll"] if today_snaps else end_bank
        peak       = max(p["bankroll"] for p in portfolio)
        drawdown   = (peak - end_bank) / peak * 100 if peak > 0 else 0
    else:
        day_start = day_end = 1000.0
        drawdown  = 0.0

    day_pnl     = day_end - day_start
    day_pnl_pct = (day_pnl / day_start * 100) if day_start > 0 else 0

    wins   = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
    total_fees = sum(
        (t.get("taker_fee", 0) or 0) + (t.get("gas_cost", 0) or 0)
        for t in closed
    )

    # Journal breakdown
    executed = [j for j in journal if j.get("outcome") == "executed"]
    rej_opus = [j for j in journal if j.get("outcome") == "rejected_opus"]
    rej_risk = [j for j in journal if j.get("outcome") == "rejected_risk"]

    lines = []
    add   = lines.append

    # ── HEADER ────────────────────────────────────────────────────────
    add(f"# Polymarket Paper Trading Bot — Daily Report")
    add(f"**Date:** {target_date}  |  **Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    add("")

    # ── PORTFOLIO SUMMARY ─────────────────────────────────────────────
    add("## Portfolio Summary")
    add("")
    add(f"| Metric | Value |")
    add(f"|--------|-------|")
    add(f"| Day Start Bankroll | ${day_start:,.2f} |")
    add(f"| Day End Bankroll   | ${day_end:,.2f} |")
    add(f"| Day PnL            | {_usd(day_pnl)} ({day_pnl_pct:+.1f}%) |")
    add(f"| Peak Drawdown      | {drawdown:.1f}% |")
    add(f"| Closed Trades      | {len(closed)} |")
    add(f"| Open Positions     | {len(open_)} |")
    add(f"| Win Rate           | {win_rate:.1f}% ({len(wins)}W / {len(losses)}L) |")
    add(f"| Total PnL (closed) | {_usd(total_pnl)} |")
    add(f"| Total Fees Paid    | ${total_fees:.3f} |")
    add("")

    # ── DECISION PIPELINE SUMMARY ─────────────────────────────────────
    add("## Decision Pipeline")
    add("")
    add("Every trade opportunity goes through 4 gates. Here is today's count:")
    add("")
    add(f"```")
    add(f"Consensus reached    : {len(journal)}")
    add(f"  → Risk block       : {len(rej_risk)}")
    add(f"  → Opus REJECTED    : {len(rej_opus)}")
    add(f"  → EXECUTED         : {len(executed)}")
    add(f"```")
    add("")
    add("**Opus 4.6 filter rate:** "
        f"{len(rej_opus)}/{len(rej_opus)+len(executed)} "
        f"({'—' if not (rej_opus or executed) else f'{len(rej_opus)/(len(rej_opus)+len(executed))*100:.0f}%'} rejected)")
    add("")

    # ── EXECUTED TRADES ───────────────────────────────────────────────
    add("## Executed Trades")
    add("")
    if not executed:
        add("_No trades executed today._")
    else:
        for i, j in enumerate(executed, 1):
            # Match with paper_trades for PnL outcome
            matching_trade = next(
                (t for t in closed if t.get("condition_id") == j["condition_id"]), None
            )
            outcome_str = ""
            if matching_trade:
                pnl = matching_trade.get("pnl", 0) or 0
                outcome_str = f"  **Outcome:** {'✅ WIN' if pnl > 0 else '❌ LOSS'} {_usd(pnl)}"

            add(f"### Trade {i}: {j.get('direction')} — {j.get('question', 'Unknown')[:90]}")
            add("")
            add(f"| Field | Value |")
            add(f"|-------|-------|")
            add(f"| Time       | {j.get('logged_at', '')[:19]} UTC |")
            add(f"| Direction  | **{j.get('direction')}** |")
            add(f"| Size       | ${j.get('proposed_size', 0):.2f} USDC |")
            add(f"| Entry      | {j.get('entry_price', 0):.3f} ({(j.get('entry_price',0)*100):.1f}% implied prob) |")
            add(f"| Avg Edge   | {j.get('avg_edge', 0):.1f}% |")
            add(f"| Confidence | {_pct(j.get('avg_confidence'))} |")
            add(f"| Volume     | ${j.get('market_volume', 0):,.0f} |")
            if outcome_str:
                add(f"| Result     | {outcome_str} |")
            add("")

            # Agent signals
            add("**Why agents agreed:**")
            add("")
            try:
                signals = json.loads(j.get("agent_signals", "[]"))
                for s in signals:
                    add(f"- **{s.get('agent')}** (edge={s.get('edge_pct',0):.1f}%, "
                        f"conf={_pct(s.get('confidence'))}): {s.get('reason','')}")
            except Exception:
                add(f"- Agents: {j.get('agent_sources', '—')}")
            add("")

            # Opus reasoning
            add(f"**Opus 4.6 verdict:** ✅ APPROVED")
            add(f"> {j.get('opus_reasoning', '—')}")
            add("")

    # ── OPUS REJECTIONS ───────────────────────────────────────────────
    add("## Opus 4.6 Rejections")
    add("")
    add("_These were blocked by Opus after passing all agent and risk checks._")
    add("_Review these to understand what Opus is filtering out._")
    add("")
    if not rej_opus:
        add("_No rejections today._")
    else:
        for i, j in enumerate(rej_opus, 1):
            add(f"### Rejected {i}: {j.get('direction')} — {j.get('question','')[:90]}")
            add("")
            add(f"- **Agents:** {j.get('agent_sources', '—')}")
            add(f"- **Size:** ${j.get('proposed_size', 0):.2f} | "
                f"**Edge:** {j.get('avg_edge', 0):.1f}% | "
                f"**Entry:** {j.get('entry_price', 0):.3f}")
            add("")

            try:
                signals = json.loads(j.get("agent_signals", "[]"))
                add("  Agent signals:")
                for s in signals:
                    add(f"  - {s.get('agent')}: {s.get('reason','')}")
            except Exception:
                pass
            add("")
            add(f"  **Opus reasoning:** {j.get('opus_reasoning', '—')}")
            add("")

    # ── RISK BLOCKS ───────────────────────────────────────────────────
    if rej_risk:
        add("## Risk Manager Blocks")
        add("")
        for j in rej_risk:
            add(f"- **{j.get('direction')}** on {j.get('question','')[:70]} "
                f"— {j.get('opus_reasoning', '—')}")
        add("")

    # ── CLOSED TRADE OUTCOMES ─────────────────────────────────────────
    add("## Closed Trade Outcomes")
    add("")
    if not closed:
        add("_No trades closed today._")
    else:
        add("| Market | Dir | Size | Entry | Exit | PnL | Fees | Agent |")
        add("|--------|-----|------|-------|------|-----|------|-------|")
        for t in closed:
            pnl  = t.get("pnl", 0) or 0
            icon = "✅" if pnl > 0 else "❌"
            add(
                f"| {str(t.get('question',''))[:40]}… "
                f"| {t.get('direction')} "
                f"| ${t.get('size_usdc',0):.2f} "
                f"| {t.get('fill_price',0):.3f} "
                f"| {t.get('exit_price',0):.3f} "
                f"| {icon} {_usd(pnl)} "
                f"| ${(t.get('taker_fee',0) or 0)+(t.get('gas_cost',0) or 0):.3f} "
                f"| {t.get('agent_source','—')} |"
            )
        add("")

    # ── AGENT PERFORMANCE ─────────────────────────────────────────────
    add("## Agent Performance (all-time)")
    add("")
    all_closed = [t for t in all_trades if t.get("status") == "closed"]
    if all_closed:
        agent_stats: dict = {}
        for t in all_closed:
            for ag in (t.get("agent_source", "") or "").split("+"):
                ag = ag.strip()
                if not ag:
                    continue
                if ag not in agent_stats:
                    agent_stats[ag] = {"trades": 0, "wins": 0, "pnl": 0.0}
                pnl = t.get("pnl", 0) or 0
                agent_stats[ag]["trades"] += 1
                agent_stats[ag]["pnl"] += pnl
                if pnl > 0:
                    agent_stats[ag]["wins"] += 1

        add("| Agent Combo | Trades | Wins | Win% | Total PnL |")
        add("|-------------|--------|------|------|-----------|")
        for ag, st in sorted(agent_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = st["wins"] / st["trades"] * 100 if st["trades"] else 0
            add(f"| {ag} | {st['trades']} | {st['wins']} | {wr:.0f}% | {_usd(st['pnl'])} |")
        add("")
    else:
        add("_No closed trades yet._")
        add("")

    # ── OPUS FILTER ANALYSIS ──────────────────────────────────────────
    add("## Opus 4.6 Filter Analysis (all-time)")
    add("")
    from core.database import get_trade_journal as _gtj
    all_journal = _gtj(limit=2000)
    all_exec    = [j for j in all_journal if j.get("outcome") == "executed"]
    all_rej_op  = [j for j in all_journal if j.get("outcome") == "rejected_opus"]
    total_seen  = len(all_exec) + len(all_rej_op)
    filter_rate = len(all_rej_op) / total_seen * 100 if total_seen else 0

    add(f"- Total opportunities Opus reviewed: **{total_seen}**")
    add(f"- Approved: **{len(all_exec)}** | Rejected: **{len(all_rej_op)}**")
    add(f"- Opus filter rate: **{filter_rate:.1f}%**")
    add("")

    # Opus rejection reasons summary
    if all_rej_op:
        add("**Common Opus rejection themes:**")
        add("")
        for j in all_rej_op[-10:]:
            add(f"- [{j.get('direction')}] {j.get('question','')[:60]}…")
            add(f"  > {j.get('opus_reasoning','—')}")
        add("")

    # ── IMPROVEMENT RECOMMENDATIONS ───────────────────────────────────
    add("## Improvement Notes")
    add("")

    issues = []

    if total_fees > abs(total_pnl) and closed:
        issues.append("⚠️  **Fees exceed PnL** — consider raising `min_edge` threshold "
                       "or reducing trade frequency.")

    if filter_rate > 70:
        issues.append("⚠️  **Opus rejecting >70% of trades** — agent signals may be too noisy. "
                       "Review agent reasoning patterns in the Rejections section above.")

    if filter_rate < 10 and total_seen > 5:
        issues.append("ℹ️  **Opus approving nearly everything** — consider tightening "
                       "the Opus prompt or raising the consensus threshold.")

    if win_rate > 0 and win_rate < 40 and len(closed) >= 5:
        issues.append("⚠️  **Win rate below 40%** — review which agent combinations "
                       "are generating losing trades. Raise `min_confidence` threshold.")

    if len(rej_opus) > len(executed) * 2:
        issues.append("ℹ️  **Many Opus rejections vs executions** — use the Rejections "
                       "section to identify if a specific agent is generating bad signals.")

    if not issues:
        issues.append("✅ No major issues detected. Keep collecting data.")

    for note in issues:
        add(note)
    add("")

    # ── FOOTER ────────────────────────────────────────────────────────
    add("---")
    add("_Generated by Polymarket Paper Trading Bot | Paper trading only — not financial advice_")

    return "\n".join(lines)


def save_report(target_date: str = None) -> Path:
    if target_date is None:
        target_date = date.today().isoformat()

    content  = generate(target_date)
    out_path = REPORTS_DIR / f"report_{target_date}.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"\nReport saved to: {out_path}")
    print("-" * 60)
    print(content)
    return out_path


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    save_report(d)
