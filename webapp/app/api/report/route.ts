import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";

// Uses service-role key so it can write the report back to Supabase
function getAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY!;
  return createClient(url, key);
}

export async function GET(req: NextRequest) {
  // Verify this is called by Vercel cron (or manually with the secret)
  const secret = process.env.CRON_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  const db = getAdminClient();
  const today = new Date().toISOString().slice(0, 10);

  // ── Fetch all data ────────────────────────────────────────────────────────
  const [tradesRes, portfolioRes, journalRes] = await Promise.all([
    db.from("paper_trades").select("*").order("opened_at", { ascending: false }).limit(500),
    db.from("portfolio_snapshots").select("*").order("timestamp", { ascending: true }).limit(2000),
    db.from("trade_journal").select("*").order("logged_at", { ascending: false }).limit(500),
  ]);

  const trades    = tradesRes.data    ?? [];
  const portfolio = portfolioRes.data ?? [];
  const journal   = journalRes.data   ?? [];

  // ── Today's slices ────────────────────────────────────────────────────────
  const todayTrades = trades.filter(
    (t) => t.opened_at?.startsWith(today) || t.closed_at?.startsWith(today)
  );
  const closed = todayTrades.filter((t) => t.status === "closed");
  const open   = todayTrades.filter((t) => t.status === "open");

  const todayJournal  = journal.filter((j) => j.logged_at?.startsWith(today));
  const executed      = todayJournal.filter((j) => j.outcome === "executed");
  const rejectedOpus  = todayJournal.filter((j) => j.outcome === "rejected_opus");
  const rejectedRisk  = todayJournal.filter((j) => j.outcome === "rejected_risk");

  // ── Portfolio stats ───────────────────────────────────────────────────────
  const todaySnaps  = portfolio.filter((p) => p.timestamp?.startsWith(today));
  const dayStart    = todaySnaps[0]?.bankroll  ?? portfolio[0]?.bankroll  ?? 1000;
  const dayEnd      = todaySnaps.at(-1)?.bankroll ?? portfolio.at(-1)?.bankroll ?? 1000;
  const dayPnl      = dayEnd - dayStart;
  const dayPnlPct   = dayStart > 0 ? (dayPnl / dayStart) * 100 : 0;
  const peak        = portfolio.length ? Math.max(...portfolio.map((p) => p.bankroll)) : dayEnd;
  const drawdown    = peak > 0 ? ((peak - dayEnd) / peak) * 100 : 0;

  const wins      = closed.filter((t) => (t.pnl ?? 0) > 0);
  const losses    = closed.filter((t) => (t.pnl ?? 0) <= 0);
  const winRate   = closed.length ? (wins.length / closed.length) * 100 : 0;
  const totalPnl  = closed.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const totalFees = closed.reduce((s, t) => s + (t.taker_fee ?? 0) + (t.gas_cost ?? 0), 0);

  // All-time Opus filter rate
  const allExec = journal.filter((j) => j.outcome === "executed").length;
  const allRej  = journal.filter((j) => j.outcome === "rejected_opus").length;
  const filterRate = allExec + allRej > 0
    ? ((allRej / (allExec + allRej)) * 100).toFixed(0)
    : "—";

  // Agent performance (all-time closed)
  const allClosed = trades.filter((t) => t.status === "closed");
  const agentStats: Record<string, { trades: number; wins: number; pnl: number }> = {};
  for (const t of allClosed) {
    for (const ag of (t.agent_source ?? "").replace("+", ",").split(",")) {
      const name = ag.trim();
      if (!name) continue;
      if (!agentStats[name]) agentStats[name] = { trades: 0, wins: 0, pnl: 0 };
      agentStats[name].trades++;
      agentStats[name].pnl += t.pnl ?? 0;
      if ((t.pnl ?? 0) > 0) agentStats[name].wins++;
    }
  }

  // ── Generate markdown ─────────────────────────────────────────────────────
  const usd  = (v: number) => `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`;
  const pct  = (v: number | null) => v != null ? `${(v * 100).toFixed(0)}%` : "—";
  const now  = new Date().toUTCString();

  const lines: string[] = [
    `# Polymarket Paper Trading — Daily Report`,
    `**Date:** ${today}  |  **Generated:** ${now}`,
    ``,
    `## Portfolio Summary`,
    ``,
    `| Metric | Value |`,
    `|--------|-------|`,
    `| Day Start Bankroll | $${dayStart.toLocaleString("en", { minimumFractionDigits: 2 })} |`,
    `| Day End Bankroll   | $${dayEnd.toLocaleString("en", { minimumFractionDigits: 2 })} |`,
    `| Day PnL            | ${usd(dayPnl)} (${dayPnlPct.toFixed(1)}%) |`,
    `| Peak Drawdown      | ${drawdown.toFixed(1)}% |`,
    `| Closed Trades      | ${closed.length} |`,
    `| Open Positions     | ${open.length} |`,
    `| Win Rate           | ${winRate.toFixed(1)}% (${wins.length}W / ${losses.length}L) |`,
    `| Total PnL (closed) | ${usd(totalPnl)} |`,
    `| Total Fees Paid    | $${totalFees.toFixed(3)} |`,
    ``,
    `## Decision Pipeline`,
    ``,
    `\`\`\``,
    `Consensus reached    : ${todayJournal.length}`,
    `  → Risk block       : ${rejectedRisk.length}`,
    `  → Opus REJECTED    : ${rejectedOpus.length}`,
    `  → EXECUTED         : ${executed.length}`,
    `\`\`\``,
    ``,
    `**All-time Opus filter rate:** ${filterRate}%`,
    ``,
    `## Executed Trades`,
    ``,
  ];

  if (!executed.length) {
    lines.push("_No trades executed today._");
  } else {
    for (const j of executed) {
      lines.push(`### ${j.direction} — ${(j.question ?? "").slice(0, 80)}`);
      lines.push(`| Field | Value |`);
      lines.push(`|-------|-------|`);
      lines.push(`| Time       | ${j.logged_at?.slice(0, 19)} UTC |`);
      lines.push(`| Size       | $${(j.proposed_size ?? 0).toFixed(2)} USDC |`);
      lines.push(`| Entry      | ${(j.entry_price ?? 0).toFixed(3)} (${((j.entry_price ?? 0) * 100).toFixed(1)}% implied prob) |`);
      lines.push(`| Avg Edge   | ${(j.avg_edge ?? 0).toFixed(1)}% |`);
      lines.push(`| Confidence | ${pct(j.avg_confidence)} |`);
      lines.push(``);
      lines.push(`**Opus verdict:** ✅ APPROVED`);
      lines.push(`> ${j.opus_reasoning ?? "—"}`);
      lines.push(``);
    }
  }

  lines.push(`## Opus Rejections`);
  lines.push(``);
  if (!rejectedOpus.length) {
    lines.push("_No rejections today._");
  } else {
    for (const j of rejectedOpus) {
      lines.push(`- **[${j.direction}]** ${(j.question ?? "").slice(0, 70)}`);
      lines.push(`  > ${j.opus_reasoning ?? "—"}`);
    }
  }

  lines.push(``);
  lines.push(`## Agent Performance (all-time)`);
  lines.push(``);
  lines.push(`| Agent | Trades | Win% | PnL |`);
  lines.push(`|-------|--------|------|-----|`);
  for (const [ag, s] of Object.entries(agentStats).sort((a, b) => b[1].pnl - a[1].pnl)) {
    const wr = s.trades ? (s.wins / s.trades * 100).toFixed(0) : "0";
    lines.push(`| ${ag} | ${s.trades} | ${wr}% | ${usd(s.pnl)} |`);
  }
  if (!Object.keys(agentStats).length) lines.push("| — | — | — | — |");

  // Improvement notes
  lines.push(``);
  lines.push(`## Improvement Notes`);
  lines.push(``);
  const notes: string[] = [];
  if (totalFees > Math.abs(totalPnl) && closed.length) notes.push("⚠️ **Fees exceed PnL** — consider raising `min_edge` threshold.");
  if (Number(filterRate) > 70) notes.push("⚠️ **Opus rejecting >70%** — agent signals may be too noisy.");
  if (Number(filterRate) < 10 && allExec + allRej > 5) notes.push("ℹ️ **Opus approving nearly everything** — consider tightening the Opus prompt.");
  if (winRate > 0 && winRate < 40 && closed.length >= 5) notes.push("⚠️ **Win rate below 40%** — raise `min_confidence` threshold.");
  if (!notes.length) notes.push("✅ No major issues detected. Keep collecting data.");
  lines.push(...notes);

  lines.push(``);
  lines.push(`---`);
  lines.push(`_Generated by Vercel cron — paper trading only, not financial advice_`);

  const reportMd = lines.join("\n");

  // ── Save to Supabase ───────────────────────────────────────────────────────
  const { error } = await db.from("daily_reports").upsert(
    { date: today, report_md: reportMd, generated_at: new Date().toISOString() },
    { onConflict: "date" }
  );

  if (error) {
    console.error("Failed to save report:", error.message);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true, date: today, lines: lines.length });
}
