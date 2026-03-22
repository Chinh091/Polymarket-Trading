"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { format } from "date-fns";
import type { PortfolioSnapshot } from "@/lib/types";

interface Props {
  data: PortfolioSnapshot[];
}

interface TooltipPayload {
  value: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs font-mono">
      <p className="text-zinc-400">{label}</p>
      <p className="text-zinc-100">${payload[0].value.toLocaleString("en", { minimumFractionDigits: 2 })}</p>
    </div>
  );
}

export function PortfolioChart({ data }: Props) {
  const formatted = data.map((d) => ({
    time: format(new Date(d.timestamp), "MM/dd HH:mm"),
    bankroll: d.bankroll,
  }));

  const start = formatted[0]?.bankroll ?? 1000;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={formatted} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: "#71717a", fontFamily: "var(--font-geist-mono)" }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 10, fill: "#71717a", fontFamily: "var(--font-geist-mono)" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          width={56}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={start} stroke="#3f3f46" strokeDasharray="3 3" />
        <Line
          type="monotone"
          dataKey="bankroll"
          stroke="#34d399"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 3, fill: "#34d399" }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export default PortfolioChart;
