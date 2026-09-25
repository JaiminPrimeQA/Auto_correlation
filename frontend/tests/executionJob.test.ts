import { describe, expect, it } from "vitest";
import type { CollectionInspection, PostmanVariable } from "@/lib/api";
import {
  failureGuidance,
  isTerminal,
  missingValues,
  STAGES,
  stageLabel,
  stageStatus,
  suppliedValuesFor,
  variablesToAsk,
} from "@/lib/executionJob";

function variable(name: string, source: PostmanVariable["source"], sensitive = false): PostmanVariable {
  return { name, source, sensitive, locations: [`Req.${name}`] };
}

function inspection(variables: PostmanVariable[], unresolved: string[]): CollectionInspection {
  return {
    collection_name: "C",
    folders: [],
    variables,
    unresolved_variable_names: unresolved,
    request_count_estimate: 1,
    target_domains: [],
    domain_warnings: [],
    unsupported_features: [],
    warnings: [],
  };
}

describe("stages", () => {
  it("lists the backend stages in execution order", () => {
    expect(STAGES.map((s) => s.state)).toEqual([
      "queued", "validating", "running_baseline", "running_comparison", "analyzing",
    ]);
  });

  it("names the actual stage in plain language", () => {
    expect(stageLabel("running_baseline")).toMatch(/run a/i);
    expect(stageLabel("running_comparison")).toMatch(/run b/i);
    expect(stageLabel("validating")).toMatch(/destination/i);
  });

  it("marks stages done, current, or pending from the job state", () => {
    const job = { state: "running_comparison", stage_history: ["validating", "running_baseline", "running_comparison"] } as const;
    expect(stageStatus("validating", job)).toBe("done");
    expect(stageStatus("running_baseline", job)).toBe("done");
    expect(stageStatus("running_comparison", job)).toBe("current");
    expect(stageStatus("analyzing", job)).toBe("pending");
  });

  it("marks the stage a failed job stopped in as failed", () => {
    const job = { state: "failed", stage_history: ["validating", "running_baseline", "failed"] } as const;
    expect(stageStatus("validating", job)).toBe("done");
    expect(stageStatus("running_baseline", job)).toBe("failed");
    expect(stageStatus("running_comparison", job)).toBe("pending");
  });

  it("a job stopped while still queued marks the queue stage as failed", () => {
    const job = { state: "cancelled", stage_history: ["cancelled"] } as const;
    expect(stageStatus("queued", job)).toBe("failed");
    expect(stageStatus("validating", job)).toBe("pending");
  });

  it("a ready job has every stage done", () => {
    const job = { state: "ready", stage_history: ["validating", "running_baseline", "running_comparison", "analyzing", "ready"] } as const;
    expect(STAGES.every((s) => stageStatus(s.state, job) === "done")).toBe(true);
  });

  it("knows which states are terminal", () => {
    expect(isTerminal("ready")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("cancelled")).toBe(true);
    expect(isTerminal("expired")).toBe(true);
    expect(isTerminal("running_baseline")).toBe(false);
    expect(isTerminal("queued")).toBe(false);
  });
});

describe("failureGuidance", () => {
  it("gives specific corrective guidance for known codes", () => {
    expect(failureGuidance("destination_validation_failed")).toMatch(/public https/i);
    expect(failureGuidance("timeout")).toMatch(/folder|smaller/i);
    expect(failureGuidance("runner_unavailable")).toMatch(/docker/i);
  });

  it("falls back to general guidance for unknown or missing codes", () => {
    expect(failureGuidance("something_new")).toBeTruthy();
    expect(failureGuidance(null)).toBeTruthy();
  });
});

describe("variables", () => {
  it("asks only for unresolved variables, keeping their sensitivity", () => {
    const data = inspection(
      [variable("host", "unresolved"), variable("api_key", "unresolved", true), variable("token", "script"),
       variable("base", "environment"), variable("$guid", "dynamic")],
      ["api_key", "host"],
    );
    expect(variablesToAsk(data).map((v) => [v.name, v.sensitive])).toEqual([["api_key", true], ["host", false]]);
  });

  it("reports names that are missing or blank", () => {
    expect(missingValues(["a", "b", "c"], { a: "1", b: "   " })).toEqual(["b", "c"]);
    expect(missingValues(["a"], { a: "x" })).toEqual([]);
  });

  it("sends exactly the asked-for values, untrimmed", () => {
    expect(suppliedValuesFor(["a", "b"], { a: " 1 ", b: "2", stale: "x" })).toEqual({ a: " 1 ", b: "2" });
  });
});
