interface Props {
  label: string;
  value: string;
  highlight?: "green" | "red";
}

export default function StatCard({ label, value, highlight }: Props) {
  const valueColor =
    highlight === "green"
      ? "text-emerald-400"
      : highlight === "red"
      ? "text-red-400"
      : "text-zinc-100";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3">
      <p className="text-[11px] font-mono uppercase tracking-widest text-zinc-500 mb-1">
        {label}
      </p>
      <p className={`text-xl font-medium tabular-nums ${valueColor}`}>{value}</p>
    </div>
  );
}
