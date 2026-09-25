// Pure helpers for the "Run a Postman collection" wizard: stage copy, progress
// status, failure guidance, and which variable values to ask for.

import type { CollectionInspection, ExecutionJobState, PostmanVariable } from "./api";

export const POLL_INTERVAL_MS = 1500;

/** Limits shown on the review step; mirror backend Settings (spec §9). */
export const RUN_LIMITS = {
  runs: 2,
  minutesPerRun: 5,
  maxReportMiB: 25,
};

export const STAGES: { state: ExecutionJobState; label: string }[] = [
  { state: "queued", label: "Waiting for a free runner" },
  { state: "validating", label: "Checking every request destination is public HTTPS" },
  { state: "running_baseline", label: "Run A — executing the collection (baseline)" },
  { state: "running_comparison", label: "Run B — executing again from the same starting values (comparison)" },
  { state: "analyzing", label: "Comparing both runs and finding correlations" },
];

const TERMINAL: ReadonlySet<ExecutionJobState> = new Set(["ready", "failed", "cancelled", "expired"]);

export function isTerminal(state: ExecutionJobState): boolean {
  return TERMINAL.has(state);
}

export function stageLabel(state: ExecutionJobState): string {
  return STAGES.find((s) => s.state === state)?.label ?? state.replaceAll("_", " ");
}

export type StageStatus = "done" | "current" | "failed" | "pending";

/** Where one stage stands for a job. `queued` is always done once any later
 * stage started; the stage a failed/cancelled job stopped in is `failed`. */
export function stageStatus(
  stage: ExecutionJobState,
  job: { state: ExecutionJobState; stage_history: readonly ExecutionJobState[] },
): StageStatus {
  const order = STAGES.map((s) => s.state);
  const index = order.indexOf(stage);
  if (job.state === "ready") return "done";
  if (job.state === stage) return "current";
  const reached = job.stage_history.filter((s) => order.includes(s) && s !== "queued");
  const stopped = isTerminal(job.state);
  // No stage past the queue reached yet: the job is (or stopped while) queued.
  const last = reached.length ? order.indexOf(reached[reached.length - 1]) : 0;
  if (index < last) return "done";
  if (index === last) return stopped ? "failed" : "current";
  return "pending";
}

const GUIDANCE: Record<string, string> = {
  destination_validation_failed:
    "Every request must go to a public HTTPS address. Fix the host variables (no localhost, private or metadata IPs, no plain http), then try again.",
  runner_unavailable:
    "The Newman runner is not available. Make sure Docker is running and the baseline11/newman:6.2.2 image is built, then retry.",
  timeout:
    "A run took longer than the 5-minute limit. Choose a smaller folder or check whether an endpoint is hanging, then retry.",
  resource_limit:
    "A run used too much memory or too many processes. Try a smaller folder or simplify heavy scripts.",
  missing_report:
    "Newman finished without a report. Check that the collection has at least one runnable request.",
  report_too_large: "A run produced a report over 25 MiB. Choose a smaller folder.",
  malformed_report: "Newman produced a report that could not be read. Retry; if it repeats, report the collection.",
  invalid_folder: "The selected folder can no longer be run. Go back and pick another folder or the whole collection.",
  invalid_destination:
    "A validated host could not be pinned safely for execution. Check the host values and try again.",
  process_failed: "Newman stopped unexpectedly. Retry; if it repeats, check the collection's scripts.",
  cancelled: "The run was cancelled. Start it again when you are ready.",
  analysis_error:
    "Both runs finished but the reports could not be analysed. Check that both runs succeeded (mostly 2xx) and retry.",
};

export function failureGuidance(code: string | null | undefined): string {
  return (
    (code && GUIDANCE[code]) ||
    "Check the details above, fix the collection or its values, and retry. Your files are kept; secret values must be entered again."
  );
}

/** Unresolved variables the tester must supply, sorted by name. */
export function variablesToAsk(inspection: CollectionInspection): PostmanVariable[] {
  const unresolved = new Set(inspection.unresolved_variable_names);
  return inspection.variables
    .filter((v) => unresolved.has(v.name))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function missingValues(names: string[], values: Record<string, string>): string[] {
  return names.filter((name) => !(values[name] ?? "").trim());
}

export function suppliedValuesFor(names: string[], values: Record<string, string>): Record<string, string> {
  return Object.fromEntries(names.map((name) => [name, values[name] ?? ""]));
}
