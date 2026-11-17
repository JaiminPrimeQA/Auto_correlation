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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Set<string>>(() => new Set());

  async function action(work: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function load() {
    // Only open suggestions are listed: accepted ones live on as rules
    // (Generate tab) and rejected ones are dismissed.
    const filters: Record<string, string> = { state: "suggested" };
    if (confidence) filters.confidence = confidence;
    const r = await api.listCandidates(analysisId, filters);
    setCandidates(r.items);
  }
  useEffect(() => {
    void action(load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, confidence]);

  /** Remove the row immediately; put it back where it was if the call fails. */
  async function resolve(c: Candidate, call: () => Promise<unknown>, done: string) {
    const index = candidates.findIndex((x) => x.id === c.id);
    setPending((prev) => new Set(prev).add(c.id));
    setCandidates((prev) => prev.filter((x) => x.id !== c.id));
    if (expanded === c.id) setExpanded(null);
    try {
      await call();
      setMsg(done);
    } catch (e) {
      setCandidates((prev) => {
        if (prev.some((x) => x.id === c.id)) return prev;
        const next = [...prev];
        next.splice(Math.min(Math.max(index, 0), next.length), 0, c);
        return next;
      });
      throw e;
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(c.id);
        return next;
      });
    }
  }

  async function accept(c: Candidate) {
    await resolve(c, () => api.acceptCandidate(analysisId, c.id), `Accepted \${${c.variable_name}} as a correlation rule`);
    await onRulesChanged();
  }
  async function reject(c: Candidate) {
    await resolve(c, () => api.rejectCandidate(analysisId, c.id), `Rejected \${${c.variable_name}}`);
    await onRulesChanged(); // refreshes the open-candidate count on the tab
  }
  async function acceptHigh() {
    const r = await api.acceptHigh(analysisId);
    setMsg(`Accepted ${r.accepted} high-confidence rule${r.accepted === 1 ? "" : "s"}`);
    await onRulesChanged();
    await load();
  }

  const highCount = candidates.filter((c) => c.confidence === "high").length;

  return (
    <div className="card p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold">
          Correlation candidates
          {candidates.length > 0 && <span className="ml-1.5 font-normal text-fg-muted">({candidates.length} open)</span>}
        </h3>
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
        <button
          className="btn-ghost text-xs"
          disabled={busy || highCount === 0}
          title={highCount === 0 ? "No high-confidence candidates left" : undefined}
          onClick={() => void action(acceptHigh)}
        >
          Accept all high{highCount > 0 ? ` (${highCount})` : ""}
        </button>
      </div>
      {msg && <p role="status" aria-live="polite" className="mb-2 rounded-[10px] bg-ok-soft px-3 py-1.5 text-xs text-ok">{msg}</p>}
      {error && <p role="alert" className="mb-2 rounded-[10px] bg-danger-soft px-3 py-1.5 text-xs text-danger">{error}</p>}
      {candidates.length === 0 && !busy && (
        <p className="text-sm text-fg-subtle">
          {confidence
            ? `No open ${confidence}-confidence candidates.`
            : "No open candidates. Accepted ones are listed as rules in the Generate tab."}
        </p>
      )}
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
                <button
                  className="btn-ghost text-xs"
                  aria-expanded={expanded === c.id}
                  onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                >
                  {expanded === c.id ? "Hide" : "Evidence"}
                </button>
                <button className="btn-ghost text-xs" disabled={pending.has(c.id)} onClick={() => void action(() => reject(c))}>
                  Reject
                </button>
                <button className="btn text-xs" disabled={pending.has(c.id)} onClick={() => void action(() => accept(c))}>
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
