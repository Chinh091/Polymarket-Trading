import type { StrategyParam } from "@/lib/types";

interface Props {
  params: StrategyParam[];
}

export function ParamsTable({ params }: Props) {
  if (!params.length) return <p className="text-sm text-zinc-500">No params.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-500 text-left">
            <th className="pb-2 pr-6 font-normal">Parameter</th>
            <th className="pb-2 pr-6 font-normal">Value</th>
            <th className="pb-2 pr-6 font-normal">Previous</th>
            <th className="pb-2 pr-6 font-normal">Updated</th>
            <th className="pb-2 font-normal">Reason</th>
          </tr>
        </thead>
        <tbody>
          {params.map((p) => {
            const changed =
              p.previous_value != null && p.previous_value !== p.param_value;
            return (
              <tr key={p.param_key} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="py-2 pr-6 text-zinc-300">{p.param_key}</td>
                <td className={`py-2 pr-6 tabular-nums ${changed ? "text-amber-400" : "text-zinc-100"}`}>
                  {p.param_value}
                </td>
                <td className="py-2 pr-6 tabular-nums text-zinc-500">
                  {p.previous_value ?? "-"}
                </td>
                <td className="py-2 pr-6 text-zinc-500">
                  {p.updated_at?.slice(0, 10)}
                </td>
                <td className="py-2 text-zinc-500 max-w-[240px] truncate" title={p.reason ?? ""}>
                  {p.reason ?? "-"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default ParamsTable;
