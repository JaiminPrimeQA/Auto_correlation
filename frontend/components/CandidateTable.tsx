"use client";

import { useEffect, useState } from "react";
import { WarningIcon } from "@phosphor-icons/react";
import { api, type Candidate } from "@/lib/api";
import { ConfidenceBadge } from "./Badge";

export function CandidateTable({
  analysisId,
  onRulesChanged,
}: {
  analysisId: string;
  onRulesChanged: () => void;
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [confidence, setConfidence] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    const filters: Record<string, string> = {};
    if (confidence) filters.confidence = confidence;
    const r = await api.listCandidates(analysisId, filters);
    setCandidates(r.items);
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, confidence]);

  async function accept(c: Candidate) {
    await api.acceptCandidate(analysisId, c.id);
    setMsg(`Accepted ${c.variable_name}`);
    onRulesChanged();
    load();
  }
  async function reject(c: Candidate) {
    await api.rejectCandidate(analysisId, c.id);
    load();
  }
  async function acceptHigh() {
    const r = await api.acceptHigh(analysisId);
    setMsg(`Accepted ${r.accepted} high-confidence rules`);
    onRulesChanged();
    load();
  }

  return (
    <div className="card p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold">Correlation candidates</h3>
        <select
          value={confidence}
          onChange={(e) => setConfidence(e.target.value)}
          className="rounded-[10px] bg-surface2 px-2 py-1 text-xs"
        >
          <option value="">all confidence</option>
          <option value="high">high</option>
          <option value="medium">medium</option>
          <option value="low">low</option>
        </select>
        <button className="btn-ghost text-xs" onClick={acceptHigh}>
          Accept all high
        </button>
      </div>
      {msg && <p className="mb-2 rounded-[10px] bg-ok-soft px-3 py-1.5 text-xs text-ok">{msg}</p>}
      {candidates.length === 0 && <p className="text-sm text-fg-subtle">No candidates.</p>}
      <div className="space-y-2">
        {candidates.map((c) => (
          <div key={c.id} className="rounded-[10px] border border-line bg-surface2">
            <div className="flex flex-wrap items-center gap-3 p-3">
              <ConfidenceBadge confidence={c.confidence} />
              <span className="mono text-sm text-accent-soft-ink">${`{${c.variable_name}}`}</span>
              <span className="text-xs text-fg-muted">
                {c.producer.location_type}:{c.producer.canonical_path} → {c.consumer_count} consumer(s)
              </span>
              <span className="ml-auto flex gap-2">
                <button className="btn-ghost text-xs" onClick={() => setExpanded(expanded === c.id ? null : c.id)}>
                  {expanded === c.id ? "Hide" : "Evidence"}
                </button>
                <button className="btn-ghost text-xs" onClick={() => reject(c)}>
                  Reject
                </button>
                <button className="btn text-xs" onClick={() => accept(c)}>
                  Accept
                </button>
              </span>
            </div>
            {expanded === c.id && (
              <div className="border-t border-line px-3 py-2 text-xs">
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="text-fg-muted">Extractor</div>
                    <div className="mono text-fg">
                      {c.extractor_method}: {c.extractor_expression}
                    </div>
                    <div className="mt-2 text-fg-muted">Values (masked)</div>
                    <div className="mono">
                      A: {c.evidence.baseline_value_masked}
                      {c.evidence.comparison_value_masked && <> · B: {c.evidence.comparison_value_masked}</>}
                    </div>
                  </div>
                  <div>
                    <div className="text-fg-muted">Score {c.evidence.score}</div>
                    <ul className="mt-1 space-y-0.5 text-ok">
                      {c.evidence.factors.map((f, i) => (
                        <li key={i}>+ {f}</li>
                      ))}
                    </ul>
                    <ul className="mt-1 space-y-0.5 text-warn">
                      {c.evidence.penalties.map((p, i) => (
                        <li key={i}>− {p}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                {c.warnings.length > 0 && (
                  <ul className="mt-2 text-danger">
                    {c.warnings.map((w, i) => (
                      <li key={i} className="flex items-center gap-1">
                        <WarningIcon size={14} aria-hidden />
                        {w}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="mt-2">
                  <div className="text-fg-muted">Consumers</div>
                  {c.consumers.map((con, i) => (
                    <div key={i} className="mono text-fg">
                      {con.execution_index + 1}. {con.location_type}:{con.canonical_path}
                      {con.wrapper ? ` (wrapped: ${con.wrapper.trim()})` : ""}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
