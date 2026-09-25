import type { AnalysisSummary } from "./api";

export type NextStep = "auto_correlate" | "generate" | "validate" | "done" | "blocked";

export function nextStep(
  s: Pick<AnalysisSummary, "readiness" | "auto_correlation_status" | "jmx_status" | "rule_count" | "summary">,
): NextStep {
  if (s.readiness.state === "not_ready") return "blocked";
  if (s.summary.correlations > 0 && s.auto_correlation_status !== "completed") return "auto_correlate";
  if (s.jmx_status === "validated") return "done";
  if (s.jmx_status === "generated") return "validate";
  return "generate";
}
