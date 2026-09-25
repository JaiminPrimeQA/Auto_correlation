"use client";

import { useRef, useState } from "react";
import type { CollectionInspection } from "@/lib/api";
import { RUN_LIMITS } from "@/lib/executionJob";
import type { CollectionFiles } from "./FilesStep";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-line py-2 sm:grid-cols-[12rem_1fr]">
      <dt className="text-[13px] text-fg-subtle">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** Step 3: choose the scope and confirm. Shows supplied variable NAMES only. */
export function ReviewStep({
  files,
  inspection,
  folderId,
  onFolderChange,
  suppliedNames,
  error,
  onBack,
  onSubmit,
}: {
  files: CollectionFiles;
  inspection: CollectionInspection;
  folderId: string | null;
  onFolderChange: (folderId: string | null) => void;
  suppliedNames: string[];
  error: string | null;
  onBack: () => void;
  onSubmit: () => Promise<void>;
}) {
  // A ref, not only state: two clicks in the same tick both see stale state.
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const folder = inspection.folders.find((f) => f.id === folderId) ?? null;
  const requestCount = folder ? folder.request_count : inspection.request_count_estimate;
  const blocking = inspection.domain_warnings.length > 0;

  async function run() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await onSubmit();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Scope and review</h2>
        <p className="mt-1 text-sm text-fg-muted">
          Check what will run. Requests are sent to the domains below, twice, from identical starting values.
        </p>
      </div>

      <div className="card space-y-5 p-5">
        <fieldset>
          <legend className="mb-2 text-[13px] font-medium">Scope</legend>
          <div role="radiogroup" aria-label="Scope" className="grid gap-2">
            {[{ id: null as string | null, label: "Whole collection", count: inspection.request_count_estimate },
              ...inspection.folders.map((f) => ({ id: f.id as string | null, label: f.path, count: f.request_count }))].map((o) => {
              const on = (folderId ?? null) === o.id;
              return (
                <label
                  key={o.id ?? "__all"}
                  className={`flex cursor-pointer items-center gap-3 rounded-[10px] border px-3.5 py-3 text-sm transition-colors duration-150 ${
                    on ? "border-accent bg-accent-soft/40" : "border-line hover:border-line-strong"
                  }`}
                >
                  <input
                    type="radio"
                    name="scope"
                    className="accent-[rgb(var(--accent))]"
                    checked={on}
                    onChange={() => onFolderChange(o.id)}
                  />
                  <span>{o.label}</span>
                  <span className="ml-auto text-[12.5px] text-fg-subtle">{o.count} requests</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <dl>
          <Row label="Collection">
            {inspection.collection_name} <span className="mono text-xs text-fg-subtle">({files.collection.name})</span>
          </Row>
          <Row label="Environment">
            {files.environment ? <span className="mono">{files.environment.name}</span> : <span className="text-fg-subtle">none</span>}
          </Row>
          <Row label="Requests">
            {requestCount} requests{folder ? ` in ${folder.path}` : ""} (estimate)
          </Row>
          <Row label="Target domains">
            {inspection.target_domains.length ? (
              <span className="flex flex-wrap gap-1">
                {inspection.target_domains.map((d) => (
                  <span key={d} className="badge badge-low mono">{d}</span>
                ))}
              </span>
            ) : (
              <span className="text-fg-subtle">none resolved yet (hosts come from supplied values)</span>
            )}
          </Row>
          <Row label="Values you supplied">
            {suppliedNames.length ? (
              <span className="flex flex-wrap gap-1">
                {suppliedNames.map((n) => (
                  <span key={n} className="mono rounded-md border border-line bg-surface2 px-1.5 text-xs">{n}</span>
                ))}
              </span>
            ) : (
              <span className="text-fg-subtle">none</span>
            )}
          </Row>
          <Row label="Limits">
            {RUN_LIMITS.runs} runs, {RUN_LIMITS.minutesPerRun} min per run, {RUN_LIMITS.maxReportMiB} MiB report per run, public HTTPS only, redirects not followed
          </Row>
        </dl>

        {blocking && (
          <div className="rounded-[10px] bg-danger-soft p-3 text-sm">
            <p className="font-medium text-danger">Destination problems: the job will fail validation</p>
            <ul className="mt-1 list-disc pl-5 text-fg">
              {inspection.domain_warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
            <p className="mt-1 text-xs text-fg-muted">
              Hosts that come from values you just supplied are re-checked when the job starts.
            </p>
          </div>
        )}
        {inspection.unsupported_features.length > 0 && (
          <div className="rounded-[10px] bg-warn-soft p-3 text-sm">
            <p className="font-medium text-warn">Needs review, may not run as in Postman</p>
            <ul className="mt-1 list-disc pl-5 text-fg">
              {inspection.unsupported_features.map((u, i) => (
                <li key={i}>{u.detail}{u.location ? <span className="text-fg-subtle"> ({u.location})</span> : null}</li>
              ))}
            </ul>
          </div>
        )}
        {inspection.warnings.length > 0 && (
          <ul className="list-disc pl-5 text-xs text-fg-muted">
            {inspection.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        )}

        {error && (
          <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <div className="flex justify-between gap-2">
          <button className="btn-secondary" onClick={onBack} disabled={busy}>
            Back
          </button>
          <button className="btn" onClick={run} disabled={busy}>
            {busy ? "Starting..." : "Run collection twice"}
          </button>
        </div>
      </div>
    </div>
  );
}
