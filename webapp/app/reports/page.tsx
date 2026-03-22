import { supabase } from "@/lib/supabase";
import Link from "next/link";

export const revalidate = 60;

async function getReports() {
  if (!supabase) return [];
  const { data } = await supabase
    .from("daily_reports")
    .select("date, generated_at")
    .order("date", { ascending: false })
    .limit(30);
  return data ?? [];
}

export default async function ReportsIndex() {
  const reports = await getReports();

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6">
      <div className="mb-6 flex items-center gap-4">
        <Link href="/" className="text-zinc-500 hover:text-zinc-300 text-sm font-mono">
          ← Dashboard
        </Link>
        <h1 className="text-xl font-medium tracking-tight">Daily Reports</h1>
      </div>

      {reports.length === 0 ? (
        <p className="text-zinc-500 text-sm">
          No reports yet. The first report will be generated tonight at 11 PM AEST.
          <br />
          You can also trigger one manually by visiting{" "}
          <code className="font-mono text-zinc-400">/api/report</code>.
        </p>
      ) : (
        <div className="space-y-2">
          {reports.map((r) => (
            <Link
              key={r.date}
              href={`/reports/${r.date}`}
              className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3 hover:border-zinc-600 transition-colors"
            >
              <span className="font-mono text-zinc-200">{r.date}</span>
              <span className="text-xs text-zinc-500">
                {r.generated_at?.slice(0, 16).replace("T", " ")} UTC
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
