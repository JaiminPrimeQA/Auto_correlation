import type { AnalysisSummary, CollectionInspection, ExecutionJob } from "@/lib/api";

export function jsonFile(name: string, content: unknown = {}) {
  return new File([JSON.stringify(content)], name, { type: "application/json" });
}

export function makeInspection(overrides: Partial<CollectionInspection> = {}): CollectionInspection {
  return {
    collection_name: "Checkout flow",
    folders: [
      { id: "auth", name: "Auth", path: "Auth", request_count: 2 },
      { id: "orders", name: "Orders", path: "Orders", request_count: 3 },
    ],
    variables: [
      { name: "api_key", source: "unresolved", sensitive: true, locations: ["Login.header.X-Api-Key"] },
      { name: "host", source: "unresolved", sensitive: false, locations: ["Login.url"] },
      { name: "token", source: "script", sensitive: true, locations: ["Orders.header.Authorization"] },
      { name: "region", source: "environment", sensitive: false, locations: ["Orders.url"] },
    ],
    unresolved_variable_names: ["api_key", "host"],
    request_count_estimate: 5,
    target_domains: ["api.example.com"],
    domain_warnings: [],
    unsupported_features: [],
    warnings: [],
    ...overrides,
  };
}

export function makeJob(overrides: Partial<ExecutionJob> = {}): ExecutionJob {
  return {
    job_id: "job_1",
    state: "queued",
    stage_history: [],
    warnings: [],
    error_code: null,
    error_detail: null,
    analysis_id: null,
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<AnalysisSummary> = {}): AnalysisSummary {
  return { analysis_id: "an_1", collection_name: "Checkout flow", mode: "two_run", ...overrides } as AnalysisSummary;
}
