import { describe, expect, it } from "vitest";
import { nextStep } from "@/lib/nextStep";
import type { AnalysisSummary } from "@/lib/api";
import { makeSummary } from "./fixtures";

const base: Partial<AnalysisSummary> = {
  readiness: { state: "ready", reasons: [], blockers: [], scenario_warnings: [] },
  auto_correlation_status: "not_started",
  jmx_status: null,
  rule_count: 0,
  summary: { correlations: 2, parameterizations: 0, external_credentials: 0, cookie_managed: 0, noise: 5, review_required: 0 },
};

describe("nextStep", () => {
  it("walks auto-correlate, generate, validate, done", () => {
    expect(nextStep(makeSummary(base))).toBe("auto_correlate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2 }))).toBe("generate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2, jmx_status: "generated" }))).toBe("validate");
    expect(nextStep(makeSummary({ ...base, auto_correlation_status: "completed", rule_count: 2, jmx_status: "validated" }))).toBe("done");
  });

  it("goes straight to generate when there is nothing to correlate", () => {
    expect(nextStep(makeSummary({ ...base, summary: { ...base.summary!, correlations: 0 } }))).toBe("generate");
  });

  it("is blocked when the runs are not ready", () => {
    expect(nextStep(makeSummary({ ...base, readiness: { ...base.readiness!, state: "not_ready" } }))).toBe("blocked");
  });
});
