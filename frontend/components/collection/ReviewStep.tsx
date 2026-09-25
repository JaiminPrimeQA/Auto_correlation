"use client";

import { useRef, useState } from "react";
import type { CollectionInspection } from "@/lib/api";
import { RUN_LIMITS } from "@/lib/executionJob";
import type { CollectionFiles } from "./FilesStep";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-edge/60 py-2 sm:grid-cols-[12rem_1fr]">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
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
    <div className="card space-y-5 p-6">
      <div>
        <h2 className="text-lg font-semibold">Scope and review</h2>
        <p className="mt-1 text-sm text-slate-400">
          Check what will run. Requests are sent to the domains below, twice, from identical starting values.
        </p>
      </div>

      <div>
        <label htmlFor="scope" className="text-sm font-medium">
          Scope
        </label>
        <select
          id="scope"
          value={folderId ?? ""}
          onChange={(e) => onFolderChange(e.target.value || null)}
          className="mt-1 w-full rounded-md border border-edge bg-ink/60 px-3 py-2 text-sm"
        >
          <option value="">Whole collection ({inspection.request_count_estimate} requests)</option>
          {inspection.folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.path} ({f.request_count} requests)
            </option>
          ))}
        </select>
      </div>

      <dl>
        <Row label="Collection">
          {inspection.collection_name} <span className="mono text-xs text-slate-500">({files.collection.name})</span>
        </Row>
        <Row label="Environment">
          {files.environment ? <span className="mono">{files.environment.name}</span> : <span className="text-slate-500">none</span>}
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
            <span className="text-slate-500">none resolved yet (hosts come from supplied values)</span>
          )}
        </Row>
        <Row label="Values you supplied">
          {suppliedNames.length ? (
            <span className="flex flex-wrap gap-1">
              {suppliedNames.map((n) => (
                <span key={n} className="mono rounded bg-ink/60 px-1.5 text-xs">{n}</span>
              ))}
            </span>
          ) : (
            <span className="text-slate-500">none</span>
          )}
        </Row>
        <Row label="Limits">
          {RUN_LIMITS.runs} runs · {RUN_LIMITS.minutesPerRun} min per run · {RUN_LIMITS.maxReportMiB} MiB report per run ·
          public HTTPS destinations only · redirects not followed
        </Row>
      </dl>

      {blocking && (
        <div className="rounded border border-danger/40 bg-danger/10 p-3 text-sm">
          <p className="font-medium text-danger">Destination problems — the job will fail validation</p>
          <ul className="mt-1 list-disc pl-5 text-slate-300">
            {inspection.domain_warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
          <p className="mt-1 text-xs text-slate-400">
            Hosts that come from values you just supplied are re-checked when the job starts.
          </p>
        </div>
      )}
      {inspection.unsupported_features.length > 0 && (
        <div className="rounded border border-warn/40 bg-warn/10 p-3 text-sm">
          <p className="font-medium text-warn">Needs review — may not run as in Postman</p>
          <ul className="mt-1 list-disc pl-5 text-slate-300">
            {inspection.unsupported_features.map((u, i) => (
              <li key={i}>{u.detail}{u.location ? <span className="text-slate-500"> — {u.location}</span> : null}</li>
            ))}
          </ul>
        </div>
      )}
      {inspection.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-slate-400">
          {inspection.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}

      {error && (
        <p role="alert" className="rounded bg-danger/15 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="flex justify-between gap-2">
        <button className="btn-ghost" onClick={onBack} disabled={busy}>
          Back
        </button>
        <button className="btn" onClick={run} disabled={busy}>
          {busy ? "Starting…" : "Run collection twice"}
        </button>
      </div>
    </div>
  );
}
