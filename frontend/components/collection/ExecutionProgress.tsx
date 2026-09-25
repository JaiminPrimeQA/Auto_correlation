"use client";

import { useEffect, useRef, useState } from "react";
import { CheckIcon, XIcon } from "@phosphor-icons/react";
import { api, ApiError, type AnalysisSummary, type ExecutionJob } from "@/lib/api";
import { failureGuidance, isTerminal, POLL_INTERVAL_MS, STAGES, stageStatus, type StageStatus } from "@/lib/executionJob";

function StageDot({ status }: { status: StageStatus }) {
  if (status === "done")
    return (
      <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full bg-accent text-accent-ink">
        <CheckIcon size={14} weight="bold" aria-hidden />
      </span>
    );
  if (status === "failed")
    return (
      <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full bg-danger text-accent-ink">
        <XIcon size={14} weight="bold" aria-hidden />
      </span>
    );
  if (status === "current")
    return (
      <span className="z-[1] grid h-8 w-8 flex-none place-items-center rounded-full border-2 border-accent bg-surface">
        <span className="breathe h-2.5 w-2.5 rounded-full bg-accent" style={{ animation: "b11-breathe 1.1s ease-in-out infinite" }} />
      </span>
    );
  return <span className="z-[1] h-8 w-8 flex-none rounded-full border-2 border-line bg-surface" />;
}

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
    <div className="card mx-auto max-w-2xl space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Running your collection</h2>
          <p className="mt-1 text-sm text-fg-muted">
            You can leave this page open. It moves on by itself when the analysis is ready.
          </p>
        </div>
        {active && (
          <button className="btn-secondary" onClick={cancel} disabled={cancelling}>
            {cancelling ? "Cancelling..." : "Cancel run"}
          </button>
        )}
      </div>

      <ol className="relative before:absolute before:bottom-4 before:left-[15px] before:top-4 before:w-0.5 before:bg-line">
        {STAGES.map((stage) => {
          const status = fatal && !job ? "pending" : stageStatus(stage.state, view);
          return (
            <li
              key={stage.state}
              data-status={status}
              className={`relative flex items-center gap-3.5 py-3 text-[14.5px] transition-colors duration-150 ${
                status === "pending" ? "text-fg-subtle" : status === "current" ? "font-medium text-fg" : "text-fg"
              }`}
            >
              <StageDot status={status} />
              <span>{stage.label}</span>
            </li>
          );
        })}
      </ol>

      {state === "ready" && <p role="status" className="text-sm text-ok">Both runs analysed. Opening results...</p>}
      {connectionLost && active && (
        <p role="status" className="text-xs text-warn">Lost contact with the server. Still retrying...</p>
      )}

      {failed && (
        <div role="alert" className="space-y-2 rounded-[10px] bg-danger-soft p-4 text-sm">
          <p className="font-medium text-danger">
            {fatal ? "The job could not be followed" : state === "cancelled" ? "Run cancelled" : "The run did not complete"}
          </p>
          <p className="text-fg">{fatal ?? job?.error_detail ?? "No details were reported."}</p>
          {job && job.warnings.length > 0 && (
            <ul className="list-disc pl-5 text-fg-muted">
              {job.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          <p className="text-fg-muted">{failureGuidance(fatal ? null : job?.error_code)}</p>
          {job?.error_code && (
            <p className="mono text-xs text-fg-subtle">
              code: {job.error_code}, job <span className="mono">{jobId.slice(0, 12)}</span>
            </p>
          )}
          <div className="flex gap-2 pt-1">
            <button className="btn" onClick={onRetry}>
              Retry
            </button>
            <button className="btn-secondary" onClick={onStartOver}>
              Start over
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
