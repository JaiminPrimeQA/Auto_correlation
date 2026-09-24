import type { AnalysisSummary } from "@/lib/api";
import { ReadinessPill } from "./Badge";

const BUCKETS: { key: keyof AnalysisSummary["summary"]; label: string; hint: string }[] = [
  { key: "correlations", label: "Correlations", hint: "proven producer → consumer (get an extractor)" },
  { key: "parameterizations", label: "Parameterizations", hint: "changed request inputs, no producer" },
  { key: "external_credentials", label: "External credentials", hint: "sensitive, supplied at runtime" },
  { key: "cookie_managed", label: "Cookie-managed", hint: "handled by HTTP Cookie Manager" },
  { key: "review_required", label: "Review required", hint: "ambiguous / placeholder values" },
  { key: "noise", label: "Noise", hint: "runtime/transport values, ignored" },
];

export function SummaryCounts({ summary }: { summary: AnalysisSummary }) {
  const s = summary.summary;
  const r = summary.readiness;
  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Classification summary</h3>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          correlation readiness:
          <ReadinessPill state={r.state} />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {BUCKETS.map((b) => {
          const n = s[b.key];
          const emphasize = b.key === "correlations" && n > 0;
          return (
            <div
              key={b.key}
              title={b.hint}
              className={`rounded p-2 ${emphasize ? "bg-brand/15" : "bg-ink/50"}`}
            >
              <div className={`text-2xl font-semibold ${emphasize ? "text-brand" : "text-slate-200"}`}>
                {n}
              </div>
              <div className="text-[11px] leading-tight text-slate-400">{b.label}</div>
            </div>
          );
        })}
      </div>
      {s.correlations === 0 && (
        <p className="mt-3 rounded bg-ink/50 px-3 py-2 text-xs text-slate-400">
          0 confirmed automatic correlations — no earlier response was proven to produce a value that a
          later request consumes across both runs. Nothing was fabricated.
        </p>
      )}
      {r.reasons.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-warn">
          {r.reasons.map((reason, i) => (
            <li key={i}>⚠ {reason}</li>
          ))}
        </ul>
      )}
      {r.blockers.map((b, i) => (
        <p key={i} className="mt-2 rounded bg-danger/15 px-3 py-2 text-xs text-danger">
          ⛔ {b}
        </p>
      ))}
    </div>
  );
}
