import type { Trade } from "@/lib/types";

interface Props {
  trades: Trade[];
  showPnl?: boolean;
}

function pnlColor(pnl?: number) {
  if (pnl == null) return "text-zinc-500";
  return pnl > 0 ? "text-emerald-400" : "text-red-400";
}

export function TradesTable({ trades, showPnl = false }: Props) {
  if (!trades.length) {
    return <p className="text-sm text-zinc-500">No trades.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500 text-left">
            <th className="pb-2 pr-4 font-normal">Market</th>
            <th className="pb-2 pr-4 font-normal">Dir</th>
            <th className="pb-2 pr-4 font-normal">Size</th>
            <th className="pb-2 pr-4 font-normal">Entry</th>
            {showPnl && <th className="pb-2 pr-4 font-normal">Exit</th>}
            {showPnl && <th className="pb-2 pr-4 font-normal">PnL</th>}
            <th className="pb-2 pr-4 font-normal">Agent</th>
            <th className="pb-2 font-normal">Opened</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="py-2 pr-4 max-w-[180px] truncate text-zinc-300" title={t.question}>
                {t.question?.slice(0, 45) ?? t.condition_id.slice(0, 12)}…
              </td>
              <td className="py-2 pr-4">
                <span className={t.direction === "YES" ? "text-emerald-400" : "text-red-400"}>
                  {t.direction}
                </span>
              </td>
              <td className="py-2 pr-4 tabular-nums text-zinc-300">${t.size_usdc.toFixed(2)}</td>
              <td className="py-2 pr-4 tabular-nums text-zinc-300">{t.fill_price.toFixed(3)}</td>
              {showPnl && (
                <td className="py-2 pr-4 tabular-nums text-zinc-300">
                  {t.exit_price != null ? t.exit_price.toFixed(3) : "-"}
                </td>
              )}
              {showPnl && (
                <td className={`py-2 pr-4 tabular-nums ${pnlColor(t.pnl)}`}>
                  {t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}` : "-"}
                </td>
              )}
              <td className="py-2 pr-4 text-zinc-500">{t.agent_source ?? "-"}</td>
              <td className="py-2 text-zinc-500">{t.opened_at?.slice(0, 16).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default TradesTable;
