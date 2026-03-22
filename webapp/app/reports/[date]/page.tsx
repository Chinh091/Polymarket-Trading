import { supabase } from "@/lib/supabase";
import Link from "next/link";
import { notFound } from "next/navigation";

export const revalidate = 60;

interface Props {
  params: Promise<{ date: string }>;
}

async function getReport(date: string) {
  if (!supabase) return null;
  const { data } = await supabase
    .from("daily_reports")
    .select("*")
    .eq("date", date)
    .single();
  return data;
}

export default async function ReportPage({ params }: Props) {
  const { date } = await params;
  const report = await getReport(date);
  if (!report) notFound();

  // Render markdown as preformatted text (simple, no extra deps)
  const lines = (report.report_md as string).split("\n");

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6 max-w-4xl mx-auto">
      <div className="mb-6 flex items-center gap-4">
        <Link href="/reports" className="text-zinc-500 hover:text-zinc-300 text-sm font-mono">
          ← All Reports
        </Link>
        <Link href="/" className="text-zinc-500 hover:text-zinc-300 text-sm font-mono">
          Dashboard
        </Link>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <div className="prose prose-invert prose-sm max-w-none font-mono text-sm leading-relaxed whitespace-pre-wrap">
          {lines.map((line, i) => {
            if (line.startsWith("# "))
              return <h1 key={i} className="text-xl font-semibold text-zinc-100 mb-2">{line.slice(2)}</h1>;
            if (line.startsWith("## "))
              return <h2 key={i} className="text-base font-semibold text-zinc-300 mt-6 mb-2 border-b border-zinc-700 pb-1">{line.slice(3)}</h2>;
            if (line.startsWith("### "))
              return <h3 key={i} className="text-sm font-semibold text-zinc-300 mt-4 mb-1">{line.slice(4)}</h3>;
            if (line.startsWith("| "))
              return <div key={i} className="font-mono text-xs text-zinc-400">{line}</div>;
            if (line.startsWith("> "))
              return <blockquote key={i} className="border-l-2 border-zinc-600 pl-3 text-zinc-400 italic">{line.slice(2)}</blockquote>;
            if (line.startsWith("- ") || line.startsWith("✅") || line.startsWith("⚠️") || line.startsWith("ℹ️"))
              return <p key={i} className="text-zinc-300">{line}</p>;
            if (line === "---")
              return <hr key={i} className="border-zinc-700 my-4" />;
            if (line === "")
              return <br key={i} />;
            return <p key={i} className="text-zinc-300">{line}</p>;
          })}
        </div>
      </div>
    </div>
  );
}
