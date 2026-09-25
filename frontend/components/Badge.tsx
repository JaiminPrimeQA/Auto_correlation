import type { Confidence } from "@/lib/api";

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const cls =
    confidence === "high"
      ? "badge-high"
      : confidence === "medium"
        ? "badge-medium"
        : confidence === "rejected"
          ? "badge-rejected"
          : "badge-low";
  return <span className={`badge ${cls}`}>{confidence}</span>;
}

export function StatusPill({ code }: { code: number | null }) {
  if (code == null) return <span className="badge badge-low">no resp</span>;
  const cls = code >= 500 ? "badge-rejected" : code >= 400 ? "badge-medium" : "badge-high";
  return <span className={`badge ${cls}`}>{code}</span>;
}

export function HealthPill({ level }: { level: "ok" | "warning" | "blocker" }) {
  const cls = level === "ok" ? "badge-high" : level === "warning" ? "badge-medium" : "badge-rejected";
  return <span className={`badge ${cls}`}>{level}</span>;
}

const CLASSIFICATION_STYLES: Record<string, string> = {
  correlation: "badge-high",
  parameterization: "badge-medium",
  external_credential: "badge-medium",
  cookie_managed: "badge-low",
  review_required: "badge-medium",
  noise: "badge-rejected",
};

export function ClassificationBadge({ classification }: { classification: string }) {
  const cls = CLASSIFICATION_STYLES[classification] || "badge-low";
  return <span className={`badge ${cls}`}>{classification.replace(/_/g, " ")}</span>;
}

export function ReadinessPill({ state }: { state: string }) {
  const cls =
    state === "ready" ? "badge-high" : state === "ready_with_review" ? "badge-medium" : "badge-rejected";
  const text =
    state === "ready" ? "Ready for correlation" : state === "ready_with_review" ? "Ready, review advised" : "Not ready";
  return <span className={`badge ${cls}`}>{text}</span>;
}
