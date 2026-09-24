// Typed client for the Baseline11 backend. All calls go through the Next.js
// rewrite proxy at /api/v1 (see next.config.mjs).

const BASE = "/api/v1";

export type Confidence = "high" | "medium" | "low" | "rejected";

export interface Occurrence {
  execution_id: string;
  execution_index: number;
  side: "request" | "response";
  location_type: string;
  canonical_path: string;
  key: string | null;
  value_masked: string;
  data_type: string;
  wrapper: string | null;
}

export interface Evidence {
  score: number;
  factors: string[];
  penalties: string[];
  baseline_value_masked: string;
  comparison_value_masked: string | null;
}

export interface Candidate {
  id: string;
  variable_name: string;
  producer: Occurrence;
  consumers: Occurrence[];
  consumer_count: number;
  extractor_method: string;
  extractor_expression: string;
  match_number: number;
  classification: string;
  confidence: Confidence;
  evidence: Evidence;
  warnings: string[];
  state: string;
}

export interface BusinessSignal {
  execution_id: string;
  execution_index: number;
  name: string;
  kind: string;
  detail: string;
  field_path: string | null;
  severity: string;
}

export interface Health {
  total_executions: number;
  status_distribution: Record<string, number>;
  missing_responses: number;
  network_errors: number;
  timeouts: number;
  auth_failure_ratio: number;
  server_error_ratio: number;
  newman_failures: number;
  assertion_failures: number;
  business_signals: BusinessSignal[];
  empty_datasets: number;
  level: "ok" | "warning" | "blocker";
  blockers: string[];
  warnings: string[];
}

export interface ClassificationSummary {
  correlations: number;
  parameterizations: number;
  external_credentials: number;
  cookie_managed: number;
  noise: number;
  review_required: number;
}

export interface Readiness {
  state: "ready" | "ready_with_review" | "not_ready";
  reasons: string[];
  blockers: string[];
  scenario_warnings: string[];
}

export interface Insight {
  id: string;
  classification: string;
  handling: string;
  variable_name: string | null;
  location: Occurrence;
  occurrence_count: number;
  occurrences: Occurrence[];
  value_a_masked: string;
  value_b_masked: string | null;
  differs_across_runs: boolean;
  reason: string;
  recommended_handling: string;
  warnings: string[];
}

export interface AnalysisSummary {
  analysis_id: string;
  mode: "two_run" | "single_run";
  baseline_health: Health;
  comparison_health: Health | null;
  alignment: {
    matched: number;
    missing: number;
    extra: number;
    reordered: number;
    coverage: number;
  } | null;
  candidate_count: number;
  summary: ClassificationSummary;
  insight_count: number;
  readiness: Readiness;
  jmx_status: string | null;
  auto_correlation_status: "not_started" | "running" | "completed" | "failed";
  rule_count: number;
  warnings: string[];
  blocked: boolean;
  collection_name: string;
}

export interface ExecutionSummary {
  id: string;
  index: number;
  name: string;
  method: string;
  url: string;
  status_code: number | null;
  has_response: boolean;
  item_path: string[];
}

export interface Rule {
  id: string;
  variable_name: string;
  origin: string;
  state: string;
  producer: Occurrence;
  consumers: Occurrence[];
  extractor_method: string;
  extractor_expression: string;
  match_number: number;
  default_value: string;
  confidence: Confidence;
  warnings: string[];
  expert_override: boolean;
}

export interface GraphNode {
  id: string;
  index: number;
  name: string;
  method: string;
  path: string;
  folder: string;
  status_code: number | null;
  role: "producer" | "consumer" | "both" | "none";
  produces: string[];
  consumes: string[];
  correlation_count: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  variable: string;
  response_key: string;
  producer_path: string;
  consumer_location: string;
  consumer_path: string;
  wrapper: string | null;
  value_masked: string;
  confidence: "high" | "medium" | "low";
  classification: string;
  state: "suggested" | "accepted" | "rejected" | "manual";
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  facets: {
    variables: string[];
    folders: string[];
    locations: string[];
    confidence_levels: string[];
  };
  stats: {
    apis: number;
    producers: number;
    consumers: number;
    variables: number;
    edges: number;
  };
}

export interface ProblemError {
  code: string;
  detail: string;
  errors?: { code?: string; path?: string; detail: string }[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const err: ProblemError = data;
    const detail = err.detail || err.code || "Request failed";
    const sub = (err.errors || []).map((e) => e.detail).join("; ");
    throw new Error(sub ? `${detail}: ${sub}` : detail);
  }
  return data as T;
}

export const api = {
  async createAnalysis(files: File[]): Promise<AnalysisSummary> {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    const res = await fetch(`${BASE}/analyses`, { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed");
    return data;
  },
  getAnalysis: (id: string) => request<AnalysisSummary>(`/analyses/${id}`),
  listExecutions: (id: string, run = "baseline") =>
    request<{ items: ExecutionSummary[]; total: number }>(
      `/analyses/${id}/executions?run=${run}&page_size=200`,
    ),
  getExecution: (id: string, execId: string) =>
    request<any>(`/analyses/${id}/executions/${execId}`),
  listOccurrences: (id: string, execId: string, side: "request" | "response") =>
    request<{ items: Occurrence[] }>(
      `/analyses/${id}/executions/${execId}/occurrences?side=${side}`,
    ),
  listCandidates: (id: string, filters: Record<string, string> = {}) => {
    const qs = new URLSearchParams(filters).toString();
    return request<{ items: Candidate[]; total: number }>(
      `/analyses/${id}/candidates${qs ? `?${qs}` : ""}`,
    );
  },
  getGraph: (id: string) => request<GraphData>(`/analyses/${id}/graph`),
  listInsights: (id: string, classification?: string) => {
    const qs = classification ? `?classification=${classification}` : "";
    return request<{ items: Insight[]; total: number; summary: ClassificationSummary }>(
      `/analyses/${id}/insights${qs}`,
    );
  },
  findConsumers: (id: string, producer: Occurrence) =>
    request<{
      producer: Occurrence;
      producer_request: string;
      value_masked: string;
      suggested_variable: string;
      suggested_extractor: string;
      suggested_expression: string;
      is_noise_source: boolean;
      consumers: (Occurrence & { wrapper: string | null; request_name: string; request_index: number })[];
      name_matches: (Occurrence & {
        wrapper: string | null;
        request_name: string;
        request_index: number;
        producer_field: string;
        consumer_field: string;
      })[];
    }>(
      `/analyses/${id}/find-consumers?producer_execution_id=${encodeURIComponent(producer.execution_id)}` +
        `&location_type=${encodeURIComponent(producer.location_type)}` +
        `&canonical_path=${encodeURIComponent(producer.canonical_path)}`,
    ),
  acceptCandidate: (id: string, cid: string) =>
    request<Rule>(`/analyses/${id}/candidates/${cid}/accept`, { method: "POST" }),
  autoCorrelate: (id: string) =>
    request<{ created: number; updated: number; total: number; variables: string[]; rules: Rule[] }>(
      `/analyses/${id}/auto-correlate`,
      { method: "POST" },
    ),
  acceptHigh: (id: string) =>
    request<{ accepted: number; rules: Rule[] }>(
      `/analyses/${id}/candidates/accept-high`,
      { method: "POST" },
    ),
  rejectCandidate: (id: string, cid: string) =>
    request(`/analyses/${id}/candidates/${cid}/reject`, { method: "POST" }),
  createRule: (id: string, body: unknown) =>
    request<Rule>(`/analyses/${id}/rules`, { method: "POST", body: JSON.stringify(body) }),
  updateRule: (id: string, ruleId: string, body: unknown) =>
    request<Rule>(`/analyses/${id}/rules/${ruleId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteRule: (id: string, ruleId: string) =>
    request(`/analyses/${id}/rules/${ruleId}`, { method: "DELETE" }),
  preview: (id: string, body: unknown = {}) =>
    request<any>(`/analyses/${id}/preview`, { method: "POST", body: JSON.stringify(body) }),
  generate: (id: string, body: unknown = {}) =>
    request<any>(`/analyses/${id}/generate`, { method: "POST", body: JSON.stringify(body) }),
  validate: (id: string, properties: Record<string, string> = {}) =>
    request<any>(`/analyses/${id}/validate`, {
      method: "POST",
      body: JSON.stringify({ properties }),
    }),
  downloadUrl: (id: string, kind: "jmx" | "manifest") =>
    `${BASE}/analyses/${id}/download/${kind}`,
};
