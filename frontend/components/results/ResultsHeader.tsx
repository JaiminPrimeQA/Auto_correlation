import type { AnalysisSummary } from "@/lib/api";
import { ReadinessPill } from "@/components/Badge";

const TILES: { key: keyof AnalysisSummary["summary"]; label: string; hint: string }[] = [
  { key: "correlations", label: "Correlations", hint: "Proven producer to consumer; each gets an extractor" },
  { key: "parameterizations", label: "Parameters", hint: "Changed request inputs with no producer" },
  { key: "external_credentials", label: "Credentials", hint: "Sensitive; supplied at run time" },
  { key: "cookie_managed", label: "Cookies", hint: "Handled by the HTTP Cookie Manager" },
  { key: "noise", label: "Noise", hint: "Runtime or transport values, ignored" },
  { key: "review_required", label: "Needs review", hint: "Ambiguous or placeholder values" },
];

export function ResultsHeader({ summary }: { summary: AnalysisSummary }) {
  return (
    <section className="rise mb-4">
      <div style={{ "--i": 0 } as React.CSSProperties} className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow mb-2">Analysis results / {summary.source === "har" ? "Browser recording" : "API collection"}</p>
          <h1 className="break-all text-3xl font-semibold tracking-[-0.035em]">{summary.collection_name || "Analysis"}</h1>
          <p className="mt-2 text-sm text-fg-muted">{summary.baseline_health.total_executions} recorded requests · {summary.rule_count} correlation rules · {summary.mode === "single_run" ? "Single recording" : "Two-run comparison"}</p>
        </div>
        <ReadinessPill state={summary.readiness.state} />
      </div>
      <div style={{ "--i": 1 } as React.CSSProperties} className="grid grid-cols-3 gap-2.5 lg:grid-cols-6">
        {TILES.map((t) => {
          const n = summary.summary[t.key];
          const hl = t.key === "correlations" && n > 0;
          return (
            <div key={t.key} title={t.hint}
                 className={`rounded-[10px] border px-3.5 py-3 ${hl ? "border-accent/30 bg-accent-soft/50" : "border-line bg-surface"}`}>
              <b className={`block text-[22px] font-semibold tabular-nums tracking-[-0.02em] ${hl ? "text-accent-soft-ink" : ""}`}>{n}</b>
              <span className="text-[12.5px] text-fg-subtle">{t.label}</span>
            </div>
          );
        })}
      </div>
      {summary.summary.correlations === 0 && (
        <p className="mt-3 rounded-[10px] bg-surface2 px-3 py-2 text-[13px] text-fg-muted">
          No confirmed correlations yet. Review suggested dependencies or run auto-correlation to connect values reused by later requests.
        </p>
      )}
      {summary.readiness.blockers.map((b) => (
        <p key={b} role="alert" className="mt-3 rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{b}</p>
      ))}
      {summary.readiness.reasons.map((r) => (
        <p key={r} className="mt-2 rounded-[10px] bg-warn-soft px-3 py-2 text-sm text-warn">{r}</p>
      ))}
    </section>
  );
}
