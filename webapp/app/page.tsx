import { supabase } from "@/lib/supabase";
import type { Trade, PortfolioSnapshot, JournalEntry, StrategyParam } from "@/lib/types";
import PortfolioChart from "@/components/PortfolioChart";
import TradesTable from "@/components/TradesTable";
import JournalTable from "@/components/JournalTable";
import StatCard from "@/components/StatCard";
import ParamsTable from "@/components/ParamsTable";

// Revalidate every 30 seconds
export const revalidate = 30;

async function getData() {
  if (!supabase) {
    return { trades: [], portfolio: [], journal: [], params: [] };
  }

  const [tradesRes, portfolioRes, journalRes, paramsRes] = await Promise.all([
    supabase
      .from("paper_trades")
      .select("*")
      .order("opened_at", { ascending: false })
      .limit(100),
    supabase
      .from("portfolio_snapshots")
      .select("*")
      .order("timestamp", { ascending: true })
      .limit(2000),
    supabase
      .from("trade_journal")
      .select("*")
      .order("logged_at", { ascending: false })
      .limit(50),
    supabase
      .from("strategy_params")
      .select("*")
      .order("param_key", { ascending: true }),
  ]);

  return {
    trades:    (tradesRes.data    ?? []) as Trade[],
    portfolio: (portfolioRes.data ?? []) as PortfolioSnapshot[],
    journal:   (journalRes.data   ?? []) as JournalEntry[],
    params:    (paramsRes.data    ?? []) as StrategyParam[],
  };
}

export default async function Dashboard() {
  const configured = !!supabase;
  const { trades, portfolio, journal, params } = await getData();

  const closed    = trades.filter((t) => t.status === "closed");
  const open      = trades.filter((t) => t.status === "open");
  const wins      = closed.filter((t) => (t.pnl ?? 0) > 0);
  const totalPnl  = closed.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const winRate   = closed.length ? (wins.length / closed.length) * 100 : 0;
  const bankroll  = portfolio.length ? portfolio[portfolio.length - 1].bankroll : 1000;
  const executed  = journal.filter((j) => j.outcome === "executed").length;
  const rejected  = journal.filter((j) => j.outcome === "rejected_opus").length;
  const filterPct = executed + rejected > 0
    ? ((rejected / (executed + rejected)) * 100).toFixed(0)
    : "-";

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6">
      {/* Env var warning */}
      {!configured && (
        <div className="mb-6 rounded-lg border border-amber-700 bg-amber-900/20 px-4 py-3 text-sm text-amber-300">
          <strong>Setup required:</strong> Add{" "}
          <code className="font-mono text-amber-200">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
          <code className="font-mono text-amber-200">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{" "}
          in your Vercel project settings, then redeploy.
        </div>
      )}
      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-[-0.03em]">
            Polymarket Paper Trading
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            Live dashboard · auto-refreshes every 30 s
          </p>
        </div>
        <a
          href="/reports"
          className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-mono text-zinc-300 hover:border-zinc-500 transition-colors"
        >
          Daily Reports →
        </a>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-8">
        <StatCard label="Bankroll"    value={`$${bankroll.toLocaleString("en", { minimumFractionDigits: 2 })}`} />
        <StatCard label="Total PnL"   value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`} highlight={totalPnl >= 0 ? "green" : "red"} />
        <StatCard label="Win Rate"    value={`${winRate.toFixed(1)}%`} />
        <StatCard label="Open"        value={String(open.length)} />
        <StatCard label="Closed"      value={String(closed.length)} />
        <StatCard label="Opus Filter" value={`${filterPct}%`} />
      </div>

      {/* Portfolio curve */}
      {portfolio.length > 1 && (
        <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-4 tracking-wide uppercase">
            Bankroll over time
          </h2>
          <PortfolioChart data={portfolio} />
        </div>
      )}

      {/* Open positions */}
      {open.length > 0 && (
        <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-4 tracking-wide uppercase">
            Open Positions
          </h2>
          <TradesTable trades={open} />
        </div>
      )}

      {/* Recent trade journal */}
      <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4 tracking-wide uppercase">
          Decision Journal (last 50)
        </h2>
        <JournalTable entries={journal} />
      </div>

      {/* Closed trades */}
      {closed.length > 0 && (
        <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-4 tracking-wide uppercase">
            Closed Trades
          </h2>
          <TradesTable trades={closed.slice(0, 50)} showPnl />
        </div>
      )}

      {/* Strategy params */}
      {params.length > 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-4 tracking-wide uppercase">
            Strategy Parameters
          </h2>
          <ParamsTable params={params} />
        </div>
      )}
    </div>
  );
}
