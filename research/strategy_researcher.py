"""
research/strategy_researcher.py

Autonomous strategy optimizer - inspired by Karpathy's autoresearch concept.

Runs on a schedule (default: every 6 hours) and does the following:
1. Pulls recent trade journal + portfolio history from the DB
2. Backtests parameter variants on the historical data
3. Asks Claude Opus 4.6 to reason about what parameters to change and why
4. Writes the updated parameters back to strategy_params (DB)
5. Saves a research report to research/reports/

The running bot reads strategy_params on each trade decision cycle, so any
parameter change takes effect immediately without restarting.

Usage:
    python research/strategy_researcher.py           # run once immediately
    python research/strategy_researcher.py --watch   # run every 6 h in a loop
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("StrategyResearcher")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Default parameter bounds - researcher cannot push values outside these
PARAM_BOUNDS = {
    "min_edge_arb":         (1.0,  20.0),
    "log_odds_threshold":   (0.2,   2.0),
    "kelly_fraction":       (0.05,  0.5),
    "min_consensus":        (1.0,   4.0),
    "consensus_window":     (60.0, 600.0),
    "max_position_pct":     (0.01,  0.10),
    "otm_min_ev":           (1.2,   3.0),
    "bayes_m_threshold":    (0.02,  0.15),
    "market_scan_interval": (30.0, 300.0),
    "news_poll_interval":   (30.0, 300.0),
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db():
    """Return the active DB module (Supabase or SQLite)."""
    if os.getenv("SUPABASE_URL"):
        import core.supabase_db as db
    else:
        import core.database as db
    return db


# ── Backtest ──────────────────────────────────────────────────────────────────

def _backtest_params(journal: list, params: dict) -> dict:
    """
    Lightweight replay: apply param filters to the journal and compute stats.

    We replay each journal entry through the key thresholds and ask:
    'Would this trade have been taken under the candidate params?'
    Then measure win-rate and PnL on the subset that would have been taken.
    """
    taken, wins, total_pnl = 0, 0, 0.0

    min_edge     = params.get("min_edge_arb", 5.0)
    min_conf     = params.get("log_odds_threshold", 0.5) * 0.5   # proxy for confidence
    min_consensus = int(params.get("min_consensus", 2))

    for entry in journal:
        if entry.get("outcome") != "executed":
            continue

        edge = entry.get("avg_edge", 0) or 0
        conf = entry.get("avg_confidence", 0) or 0

        # Simulate whether this trade would have been taken under candidate params
        if edge < min_edge:
            continue
        if conf < min_conf:
            continue

        # Count agents (agent_sources is comma/plus separated)
        sources = (entry.get("agent_sources") or "").replace("+", ",")
        n_agents = len([s for s in sources.split(",") if s.strip()])
        if n_agents < min_consensus:
            continue

        # Mock outcome: use actual pnl if available (comes from paper_trades join)
        pnl = entry.get("pnl", 0) or 0
        taken += 1
        total_pnl += pnl
        if pnl > 0:
            wins += 1

    win_rate = wins / taken if taken else 0
    return {
        "trades_taken": taken,
        "win_rate":     win_rate,
        "total_pnl":    total_pnl,
        "avg_pnl":      total_pnl / taken if taken else 0,
    }


def _generate_variants(base: dict) -> list[dict]:
    """
    Generate a small grid of parameter variants around the current values.
    Returns list of {'params': {...}, 'label': str}.
    """
    variants = [{"params": dict(base), "label": "baseline"}]

    # Edge threshold: tighter vs looser
    for factor, label in [(0.75, "edge−25%"), (1.25, "edge+25%")]:
        v = dict(base)
        v["min_edge_arb"] = round(
            max(PARAM_BOUNDS["min_edge_arb"][0],
                min(PARAM_BOUNDS["min_edge_arb"][1],
                    base.get("min_edge_arb", 5.0) * factor)), 2)
        variants.append({"params": v, "label": label})

    # Kelly fraction: more / less aggressive
    for factor, label in [(0.5, "kelly×0.5"), (1.5, "kelly×1.5")]:
        v = dict(base)
        v["kelly_fraction"] = round(
            max(PARAM_BOUNDS["kelly_fraction"][0],
                min(PARAM_BOUNDS["kelly_fraction"][1],
                    base.get("kelly_fraction", 0.25) * factor)), 3)
        variants.append({"params": v, "label": label})

    # Log-odds threshold: stricter signal filter
    for delta, label in [(-0.1, "log_odds−0.1"), (+0.1, "log_odds+0.1")]:
        v = dict(base)
        v["log_odds_threshold"] = round(
            max(PARAM_BOUNDS["log_odds_threshold"][0],
                min(PARAM_BOUNDS["log_odds_threshold"][1],
                    base.get("log_odds_threshold", 0.5) + delta)), 2)
        variants.append({"params": v, "label": label})

    # OTM EV minimum
    for delta, label in [(-0.2, "otm_ev−0.2"), (+0.2, "otm_ev+0.2")]:
        v = dict(base)
        v["otm_min_ev"] = round(
            max(PARAM_BOUNDS["otm_min_ev"][0],
                min(PARAM_BOUNDS["otm_min_ev"][1],
                    base.get("otm_min_ev", 1.8) + delta)), 2)
        variants.append({"params": v, "label": label})

    return variants


# ── Opus reasoning ────────────────────────────────────────────────────────────

def _call_opus_researcher(context: str) -> dict:
    """
    Ask Claude Opus 4.6 to propose new parameter values given the context.

    Returns dict of {param_key: new_value, ...} (only params to change).
    Returns {} on any error.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY - skipping Opus reasoning step")
        return {}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an autonomous trading strategy optimizer for a Polymarket paper trading bot.

Your job: analyze the performance data below and propose SPECIFIC parameter changes to improve profitability.

{context}

Respond with ONLY a JSON object in this exact format (include only params you want to change):
{{
  "changes": {{
    "param_key": new_value,
    ...
  }},
  "reasoning": "One paragraph explanation of why these changes will improve performance."
}}

Rules:
- Only propose changes if the data clearly supports them (10+ trades minimum)
- Keep changes conservative (≤25% from current value per step)
- If performance is already good (win_rate > 55%, positive PnL), say so and propose no changes
- Valid param keys: min_edge_arb, log_odds_threshold, kelly_fraction, min_consensus,
  consensus_window, max_position_pct, otm_min_ev, bayes_m_threshold
"""

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()

        # Strip markdown code block if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        return result

    except Exception as e:
        logger.error(f"Opus researcher call failed: {e}")
        return {}


# ── Main research loop ────────────────────────────────────────────────────────

def run_research_cycle() -> str:
    """
    Execute one full research cycle. Returns the path of the saved report.
    """
    db = _get_db()
    now = datetime.utcnow()
    logger.info(f"Starting research cycle at {now.isoformat()}")

    # 1. Load data
    journal = db.get_trade_journal(limit=500) if hasattr(db, "get_trade_journal") else []
    trades  = db.get_trade_history(limit=500) if hasattr(db, "get_trade_history") else []
    portfolio = db.get_portfolio_history() if hasattr(db, "get_portfolio_history") else []

    # Enrich journal entries with actual PnL from trades
    trade_map = {t.get("condition_id"): t for t in trades}
    for entry in journal:
        cid = entry.get("condition_id")
        if cid in trade_map:
            entry["pnl"] = trade_map[cid].get("pnl", 0)

    # 2. Load current params
    current_params = {}
    if hasattr(db, "get_strategy_params"):
        current_params = db.get_strategy_params()
    # Convert string values to float
    current_params = {k: float(v) for k, v in current_params.items()
                      if k in PARAM_BOUNDS}

    if not current_params:
        # Defaults if DB has no params yet
        current_params = {
            "min_edge_arb": 5.0, "log_odds_threshold": 0.5,
            "kelly_fraction": 0.25, "min_consensus": 2.0,
            "consensus_window": 300.0, "max_position_pct": 0.05,
            "otm_min_ev": 1.8, "bayes_m_threshold": 0.06,
            "market_scan_interval": 60.0, "news_poll_interval": 60.0,
        }

    # 3. Compute baseline stats
    closed = [t for t in trades if t.get("status") == "closed"]
    executed_journal = [j for j in journal if j.get("outcome") == "executed"]
    rejected_opus    = [j for j in journal if j.get("outcome") == "rejected_opus"]
    rejected_risk    = [j for j in journal if j.get("outcome") == "rejected_risk"]

    total_pnl  = sum(t.get("pnl", 0) or 0 for t in closed)
    wins       = [t for t in closed if (t.get("pnl") or 0) > 0]
    win_rate   = len(wins) / len(closed) if closed else 0
    total_fees = sum((t.get("taker_fee", 0) or 0) + (t.get("gas_cost", 0) or 0)
                     for t in closed)

    bankroll_start = portfolio[0]["bankroll"] if portfolio else 1000.0
    bankroll_end   = portfolio[-1]["bankroll"] if portfolio else 1000.0
    total_return   = (bankroll_end - bankroll_start) / bankroll_start * 100

    # 4. Backtest variants
    variants = _generate_variants(current_params)
    results  = []
    for v in variants:
        stats = _backtest_params(journal, v["params"])
        results.append({"label": v["label"], "params": v["params"], "stats": stats})

    # Sort by PnL descending (with tie-break on win-rate)
    results.sort(key=lambda r: (r["stats"]["total_pnl"], r["stats"]["win_rate"]),
                 reverse=True)
    best = results[0]

    # 5. Build context for Opus
    agent_perf: dict = {}
    for t in closed:
        for ag in (t.get("agent_source") or "").replace("+", ",").split(","):
            ag = ag.strip()
            if not ag:
                continue
            if ag not in agent_perf:
                agent_perf[ag] = {"trades": 0, "wins": 0, "pnl": 0.0}
            pnl = t.get("pnl", 0) or 0
            agent_perf[ag]["trades"] += 1
            agent_perf[ag]["pnl"]    += pnl
            if pnl > 0:
                agent_perf[ag]["wins"] += 1

    agent_lines = "\n".join(
        f"  {ag}: {s['trades']} trades, "
        f"{s['wins']/s['trades']*100:.0f}% win rate, "
        f"PnL ${s['pnl']:+.2f}"
        for ag, s in sorted(agent_perf.items(), key=lambda x: x[1]["pnl"], reverse=True)
    ) if agent_perf else "  No closed trades yet."

    backtest_lines = "\n".join(
        f"  [{r['label']}] trades={r['stats']['trades_taken']}, "
        f"win={r['stats']['win_rate']*100:.0f}%, "
        f"pnl=${r['stats']['total_pnl']:+.2f}"
        for r in results
    )

    context = f"""=== PERFORMANCE SUMMARY ===
Date: {now.date()}
Total closed trades: {len(closed)}
Win rate: {win_rate*100:.1f}%
Total PnL: ${total_pnl:+.2f}
Total fees paid: ${total_fees:.3f}
Total bankroll return: {total_return:+.1f}%
Opus filter rate: {len(rejected_opus)}/{len(rejected_opus)+len(executed_journal)} rejected

=== AGENT PERFORMANCE ===
{agent_lines}

=== CURRENT PARAMETERS ===
{json.dumps(current_params, indent=2)}

=== BACKTEST RESULTS (parameter variants vs historical trades) ===
{backtest_lines}

=== BEST BACKTEST VARIANT ===
Label: {best['label']}
Params: {json.dumps(best['params'], indent=2)}
Stats: trades={best['stats']['trades_taken']}, win={best['stats']['win_rate']*100:.0f}%, pnl=${best['stats']['total_pnl']:+.2f}
"""

    # 6. Ask Opus for reasoning + final recommendations
    opus_result = _call_opus_researcher(context)
    proposed_changes = opus_result.get("changes", {})
    reasoning = opus_result.get("reasoning", "No Opus response available.")

    # 7. Validate and clamp proposed changes
    applied_changes = {}
    for key, new_val in proposed_changes.items():
        if key not in PARAM_BOUNDS:
            logger.warning(f"Opus proposed unknown param '{key}' - skipped")
            continue
        lo, hi = PARAM_BOUNDS[key]
        clamped = max(lo, min(hi, float(new_val)))
        if clamped != float(new_val):
            logger.info(f"Clamped {key}: {new_val} → {clamped}")
        applied_changes[key] = clamped

    # 8. Save params to DB (only if we have a save function)
    if applied_changes and hasattr(db, "save_strategy_param"):
        for key, new_val in applied_changes.items():
            old_val = current_params.get(key, 0)
            try:
                db.save_strategy_param(
                    key=key, value=new_val, previous=old_val,
                    reason=f"AutoResearcher {now.date()}: {reasoning[:100]}",
                    win_rate=win_rate, pnl=total_pnl
                )
                logger.info(f"Updated param: {key} {old_val} → {new_val}")
            except Exception as e:
                logger.error(f"Failed to save param {key}: {e}")

    # 9. Write markdown report
    lines = [
        f"# Strategy Research Report - {now.date()}",
        f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Performance Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Closed Trades | {len(closed)} |",
        f"| Win Rate | {win_rate*100:.1f}% |",
        f"| Total PnL | ${total_pnl:+.2f} |",
        f"| Fees Paid | ${total_fees:.3f} |",
        f"| Bankroll Return | {total_return:+.1f}% |",
        f"| Opus Filter Rate | {len(rejected_opus)/(len(rejected_opus)+len(executed_journal))*100:.0f}% |"
        if (rejected_opus or executed_journal) else "| Opus Filter Rate | - |",
        "",
        "## Agent Performance",
        "",
        "| Agent | Trades | Win% | PnL |",
        "|-------|--------|------|-----|",
    ]
    for ag, s in sorted(agent_perf.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        lines.append(f"| {ag} | {s['trades']} | {wr:.0f}% | ${s['pnl']:+.2f} |")
    if not agent_perf:
        lines.append("| - | - | - | - |")

    lines += [
        "",
        "## Backtest Results",
        "",
        "| Variant | Trades | Win% | PnL |",
        "|---------|--------|------|-----|",
    ]
    for r in results:
        lines.append(
            f"| {r['label']} | {r['stats']['trades_taken']} "
            f"| {r['stats']['win_rate']*100:.0f}% "
            f"| ${r['stats']['total_pnl']:+.2f} |"
        )

    lines += [
        "",
        "## Opus 4.6 Reasoning",
        "",
        f"> {reasoning}",
        "",
        "## Parameter Changes Applied",
        "",
    ]
    if applied_changes:
        lines += [
            "| Parameter | Old Value | New Value |",
            "|-----------|-----------|-----------|",
        ]
        for key, new_val in applied_changes.items():
            old_val = current_params.get(key, "-")
            lines.append(f"| {key} | {old_val} | {new_val} |")
    else:
        lines.append("_No parameter changes recommended._")

    lines += [
        "",
        "---",
        "_Generated by StrategyResearcher (autoresearch loop) - Paper trading only_",
    ]

    report_text = "\n".join(lines)
    report_path = REPORTS_DIR / f"research_{now.strftime('%Y%m%d_%H%M')}.md"
    report_path.write_text(report_text, encoding="utf-8")
    logger.info(f"Research report saved: {report_path}")
    print(report_text)
    return str(report_path)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Autonomous strategy researcher")
    parser.add_argument("--watch", action="store_true",
                        help="Run every 6 hours in a loop (default: run once)")
    parser.add_argument("--interval", type=int, default=6,
                        help="Hours between research cycles when --watch is set")
    args = parser.parse_args()

    if args.watch:
        interval_sec = args.interval * 3600
        logger.info(f"Watch mode: running research every {args.interval} hours")
        while True:
            try:
                run_research_cycle()
            except Exception as e:
                logger.error(f"Research cycle failed: {e}", exc_info=True)
            logger.info(f"Next cycle in {args.interval} hours…")
            time.sleep(interval_sec)
    else:
        run_research_cycle()


if __name__ == "__main__":
    main()
