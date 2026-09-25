"use client";

import { useEffect, useState } from "react";
import { api, type Insight } from "@/lib/api";
import { ClassificationBadge } from "./Badge";

const FILTERS = [
  { id: "", label: "All" },
  { id: "parameterization", label: "Parameterization" },
  { id: "external_credential", label: "External credential" },
  { id: "cookie_managed", label: "Cookie-managed" },
  { id: "review_required", label: "Review required" },
  { id: "noise", label: "Noise" },
];

export function InsightsPanel({ analysisId }: { analysisId: string }) {
  const [items, setItems] = useState<Insight[]>([]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setBusy(true);
    api
      .listInsights(analysisId, filter || undefined)
      .then((r) => alive && setItems(r.items))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [analysisId, filter]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`rounded-[10px] px-2 py-1 text-xs ${
              filter === f.id ? "bg-accent text-accent-ink" : "bg-surface2 text-fg-muted hover:text-fg"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {busy && <p className="text-xs text-fg-muted">Loading...</p>}
      {!busy && items.length === 0 && (
        <p className="text-xs text-fg-muted">No values in this category.</p>
      )}

      <div className="space-y-2">
        {items.map((i) => (
          <div key={i.id} className="card p-3 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <ClassificationBadge classification={i.classification} />
                <span className="mono text-fg">
                  {i.location.location_type}: {i.location.key || i.location.canonical_path}
                </span>
                {i.occurrence_count > 1 && (
                  <span className="text-fg-subtle">×{i.occurrence_count} requests</span>
                )}
              </div>
              {i.differs_across_runs && (
                <span className="rounded-full bg-surface2 px-2 py-0.5 text-fg-muted">changed across runs</span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-4 text-fg-muted">
              <span>
                Run A: <span className="mono text-fg">{i.value_a_masked || "-"}</span>
              </span>
              {i.value_b_masked != null && (
                <span>
                  Run B: <span className="mono text-fg">{i.value_b_masked || "-"}</span>
                </span>
              )}
            </div>
            <p className="mt-2 text-fg">{i.reason}</p>
            <p className="mt-1 text-fg-subtle">→ {i.recommended_handling}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
