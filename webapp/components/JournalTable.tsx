import type { JournalEntry } from "@/lib/types";

interface Props {
  entries: JournalEntry[];
}

const OUTCOME_BADGE: Record<string, string> = {
  executed:      "bg-emerald-900/50 text-emerald-400 border-emerald-800",
  rejected_opus: "bg-amber-900/50 text-amber-400 border-amber-800",
  rejected_risk: "bg-red-900/50 text-red-400 border-red-800",
};

const OUTCOME_LABEL: Record<string, string> = {
  executed:      "Executed",
  rejected_opus: "Opus ✗",
  rejected_risk: "Risk ✗",
};

export function JournalTable({ entries }: Props) {
  if (!entries.length) {
    return <p className="text-sm text-zinc-500">No journal entries.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500 text-left">
            <th className="pb-2 pr-4 font-normal">Time</th>
            <th className="pb-2 pr-4 font-normal">Outcome</th>
            <th className="pb-2 pr-4 font-normal">Dir</th>
            <th className="pb-2 pr-4 font-normal">Edge</th>
            <th className="pb-2 pr-4 font-normal">Conf</th>
            <th className="pb-2 pr-4 font-normal">Agents</th>
            <th className="pb-2 font-normal">Market</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((j) => {
            const badgeClass = OUTCOME_BADGE[j.outcome] ?? "bg-zinc-800 text-zinc-400 border-zinc-700";
            return (
              <tr key={j.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="py-2 pr-4 text-zinc-500">{j.logged_at?.slice(0, 16).replace("T", " ")}</td>
                <td className="py-2 pr-4">
                  <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${badgeClass}`}>
                    {OUTCOME_LABEL[j.outcome] ?? j.outcome}
                  </span>
                </td>
                <td className="py-2 pr-4">
                  <span className={j.direction === "YES" ? "text-emerald-400" : "text-red-400"}>
                    {j.direction}
                  </span>
                </td>
                <td className="py-2 pr-4 tabular-nums text-zinc-300">{j.avg_edge?.toFixed(1)}%</td>
                <td className="py-2 pr-4 tabular-nums text-zinc-300">
                  {j.avg_confidence != null ? `${(j.avg_confidence * 100).toFixed(0)}%` : "—"}
                </td>
                <td className="py-2 pr-4 text-zinc-500 max-w-[120px] truncate" title={j.agent_sources}>
                  {j.agent_sources ?? "—"}
                </td>
                <td className="py-2 text-zinc-400 max-w-[200px] truncate" title={j.question}>
                  {j.question?.slice(0, 55)}…
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default JournalTable;
