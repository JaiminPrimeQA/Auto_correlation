"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AnalysisSummary, type ExecutionJob } from "@/lib/api";
import { failureGuidance, isTerminal, POLL_INTERVAL_MS, STAGES, stageStatus, type StageStatus } from "@/lib/executionJob";

const STATUS_ICON: Record<StageStatus, string> = { done: "✓", current: "●", failed: "✕", pending: "○" };
const STATUS_CLASS: Record<StageStatus, string> = {
  done: "text-ok",
  current: "text-brand",
  failed: "text-danger",
  pending: "text-slate-600",
};

/** Step 4: poll the job, name the real stage, and stay here on failure. */
export function ExecutionProgress({
  jobId,
  onReady,
  onRetry,
  onStartOver,
  pollMs = POLL_INTERVAL_MS,
}: {
  jobId: string;
  onReady: (summary: AnalysisSummary) => void;
  onRetry: () => void;
  onStartOver: () => void;
  pollMs?: number;
}) {
  const [job, setJob] = useState<ExecutionJob | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const current = await api.getExecutionJob(jobId);
        if (stopped) return;
        setConnectionLost(false);
        setJob(current);
        if (current.state === "ready" && current.analysis_id) {
          const summary = await api.getAnalysis(current.analysis_id);
          if (!stopped) onReadyRef.current(summary);
          return;
        }
        if (isTerminal(current.state)) return;
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status < 500) {
          setFatal(e.message);
          return;
        }
        setConnectionLost(true); // transient: keep polling
      }
      timer = setTimeout(poll, pollMs);
    }

    poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, pollMs]);

  async function cancel() {
    setCancelling(true);
    try {
      await api.deleteExecutionJob(jobId);
    } catch (e) {
      setFatal(e instanceof Error ? e.message : String(e));
    }
  }

  const state = job?.state ?? "queued";
  const view = job ?? { state, stage_history: [] };
  const failed = state === "failed" || state === "cancelled" || state === "expired" || fatal !== null;
  const active = !failed && !isTerminal(state);

  return (
    <div className="card space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Executing your collection</h2>
          <p className="mt-1 text-sm text-slate-400">
            Two isolated Newman runs, then the same analysis as uploaded reports. Job{" "}
            <span className="mono">{jobId.slice(0, 12)}</span>
          </p>
        </div>
        {active && (
          <button className="btn-ghost text-xs" onClick={cancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        )}
      </div>

      <ol className="space-y-2">
        {STAGES.map((stage) => {
          const status = fatal && !job ? "pending" : stageStatus(stage.state, view);
          return (
            <li key={stage.state} data-status={status} className="flex items-center gap-3 text-sm">
              <span className={`w-4 text-center ${STATUS_CLASS[status]} ${status === "current" ? "animate-pulse" : ""}`}>
                {STATUS_ICON[status]}
              </span>
              <span className={status === "pending" ? "text-slate-500" : "text-slate-200"}>{stage.label}</span>
            </li>
          );
        })}
      </ol>

      {state === "ready" && <p role="status" className="text-sm text-ok">Both runs analysed — opening results…</p>}
      {connectionLost && active && (
        <p role="status" className="text-xs text-warn">Lost contact with the server — still retrying…</p>
      )}

      {failed && (
        <div role="alert" className="space-y-2 rounded border border-danger/40 bg-danger/10 p-4 text-sm">
          <p className="font-medium text-danger">
            {fatal ? "The job could not be followed" : state === "cancelled" ? "Run cancelled" : "The run did not complete"}
          </p>
          <p className="text-slate-200">{fatal ?? job?.error_detail ?? "No details were reported."}</p>
          {job && job.warnings.length > 0 && (
            <ul className="list-disc pl-5 text-slate-300">
              {job.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          <p className="text-slate-400">{failureGuidance(fatal ? null : job?.error_code)}</p>
          {job?.error_code && <p className="mono text-xs text-slate-500">code: {job.error_code}</p>}
          <div className="flex gap-2 pt-1">
            <button className="btn" onClick={onRetry}>
              Retry
            </button>
            <button className="btn-ghost" onClick={onStartOver}>
              Start over
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
