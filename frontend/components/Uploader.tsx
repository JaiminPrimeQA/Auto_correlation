"use client";

import { useState } from "react";
import { api, type AnalysisSummary } from "@/lib/api";
import { HelpNote } from "./HelpNote";

export function Uploader({ onDone }: { onDone: (a: AnalysisSummary) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const summary = await api.createAnalysis(files);
      onDone(summary);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <HelpNote
        title="How this works (3 easy steps)"
        steps={[
          "Upload two Newman JSON reports of the SAME collection — a baseline run and a comparison run (or one file for lower-confidence manual mode).",
          "Review the dynamic values the tool finds, accept the good ones, or add your own.",
          "Generate and download a ready-to-run JMeter 5.6.3 test plan (correlated.jmx).",
        ]}
        tip="For automatic correlation both runs should succeed (mostly 200) and the dynamic value should be RETURNED by a response (e.g. a login) and reused later. Capture each run with fresh login/session data."
      />
      <div className="card p-6">
      <h1 className="text-xl font-semibold">Upload Newman reports</h1>
      <p className="mt-1 text-sm text-slate-400">
        Recommended: two runs of the same collection (<code className="mono">baseline.json</code> and{" "}
        <code className="mono">comparison.json</code>) captured with fresh sessions. One file enables
        lower-confidence manual mode.
      </p>

      <label
        className="mt-5 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-edge bg-ink/40 px-4 py-10 text-center hover:bg-ink"
        htmlFor="file-input"
      >
        <span className="text-sm text-slate-300">Choose 1 or 2 Newman JSON files</span>
        <span className="mt-1 text-xs text-slate-500">Max 25 MiB each · JSON only</span>
        <input
          id="file-input"
          type="file"
          accept="application/json,.json"
          multiple
          className="hidden"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 2))}
        />
      </label>

      {files.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-slate-300">
          {files.map((f) => (
            <li key={f.name} className="flex justify-between rounded bg-ink/50 px-3 py-1.5">
              <span className="mono">{f.name}</span>
              <span className="text-slate-500">{(f.size / 1024).toFixed(1)} KiB</span>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mt-3 rounded bg-danger/15 px-3 py-2 text-sm text-danger">{error}</p>}

      <button className="btn mt-5 w-full justify-center" disabled={busy || files.length === 0} onClick={submit}>
        {busy ? "Analyzing…" : "Analyze"}
      </button>
      </div>
    </div>
  );
}
