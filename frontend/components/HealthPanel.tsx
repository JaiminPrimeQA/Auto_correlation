import type { AnalysisSummary, Health } from "@/lib/api";
import { HealthPill } from "./Badge";
import { SummaryCounts } from "./SummaryCounts";

function RunHealth({ title, health }: { title: string; health: Health }) {
  const businessCount = health.business_signals.filter((s) => s.kind !== "assertion_failed").length;
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{title}</h3>
        <HealthPill level={health.level} />
      </div>

      {/* Transport health */}
      <div className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Transport health
      </div>
      <div className="mt-1 flex flex-wrap gap-2 text-xs">
        {Object.entries(health.status_distribution).map(([k, v]) => (
          <span key={k} className="rounded bg-ink/60 px-2 py-0.5 text-slate-300">
            {k}: {v}
          </span>
        ))}
      </div>
      <div className="mt-1 text-xs text-slate-400">
        auth-fail {(health.auth_failure_ratio * 100).toFixed(0)}% · server-err{" "}
        {(health.server_error_ratio * 100).toFixed(0)}% · missing {health.missing_responses} · net-err{" "}
        {health.network_errors} · timeouts {health.timeouts}
      </div>

      {/* Business / scenario health */}
      <div className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Business / scenario health
      </div>
      <div className="mt-1 text-xs text-slate-400">
        assertion failures {health.assertion_failures} · business errors {businessCount} · empty datasets{" "}
        {health.empty_datasets}
      </div>
      {businessCount > 0 && (
        <p className="mt-1 text-[11px] text-warn">
          HTTP 2xx does not prove business success — see details below.
        </p>
      )}
      <div className="mt-1 space-y-1">
        {health.business_signals.map((s, i) => (
          <div key={i} className="rounded bg-ink/50 px-2 py-1 text-[11px] text-slate-400">
            <span className="text-slate-300">#{s.execution_index + 1} {s.name}</span> — {s.kind.replace(/_/g, " ")}: {s.detail}
          </div>
        ))}
      </div>

      {health.blockers.map((b, i) => (
        <p key={i} className="mt-2 rounded bg-danger/15 px-3 py-2 text-xs text-danger">
          ⛔ {b}
        </p>
      ))}
      {health.warnings.map((w, i) => (
        <p key={i} className="mt-2 rounded bg-warn/15 px-3 py-2 text-xs text-warn">
          ⚠ {w}
        </p>
      ))}
    </div>
  );
}

export function HealthPanel({ summary }: { summary: AnalysisSummary }) {
  return (
    <div className="space-y-3">
      <SummaryCounts summary={summary} />
      {summary.warnings.map((w, i) => (
        <div key={i} className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
          ⚠ {w}
        </div>
      ))}
      {summary.readiness.scenario_warnings.map((w, i) => (
        <div key={i} className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
          ⚠ Scenario consistency: {w}
        </div>
      ))}
      {summary.blocked && (
        <div className="rounded-lg border border-danger/50 bg-danger/10 p-4 text-sm text-danger">
          <strong>Automatic correlation is blocked.</strong> The primary auto-correlated JMX action is
          not reliable for this pair. Manual review remains available, but error-body differences will
          not be treated as dynamic business correlations.
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <RunHealth title="Baseline run" health={summary.baseline_health} />
        {summary.comparison_health && (
          <RunHealth title="Comparison run" health={summary.comparison_health} />
        )}
      </div>
      {summary.alignment && (
        <div className="card flex flex-wrap gap-4 p-3 text-xs text-slate-300">
          <span>coverage: {(summary.alignment.coverage * 100).toFixed(0)}%</span>
          <span>matched: {summary.alignment.matched}</span>
          <span>missing: {summary.alignment.missing}</span>
          <span>extra: {summary.alignment.extra}</span>
          <span>reordered: {summary.alignment.reordered}</span>
        </div>
      )}
    </div>
  );
}
