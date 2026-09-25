"use client";

import { useState } from "react";
import { api, type CollectionInspection } from "@/lib/api";

export interface CollectionFiles {
  collection: File;
  environment: File | null;
}

function FilePicker({
  id,
  label,
  hint,
  file,
  onPick,
}: {
  id: string;
  label: string;
  hint: string;
  file: File | null;
  onPick: (file: File | null) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <p className="text-xs text-slate-500">{hint}</p>
      <input
        id={id}
        type="file"
        accept="application/json,.json"
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        className="mt-2 block w-full text-sm text-slate-300 file:mr-3 file:rounded-md file:border-0 file:bg-edge file:px-3 file:py-1.5 file:text-slate-200"
      />
      {file && <p className="mono mt-1 text-xs text-slate-400">{file.name} · {(file.size / 1024).toFixed(1)} KiB</p>}
    </div>
  );
}

/** Step 1: choose files and inspect them - nothing is executed here. */
export function FilesStep({
  initial,
  onInspected,
}: {
  initial?: CollectionFiles | null;
  onInspected: (files: CollectionFiles, inspection: CollectionInspection) => void;
}) {
  const [collection, setCollection] = useState<File | null>(initial?.collection ?? null);
  const [environment, setEnvironment] = useState<File | null>(initial?.environment ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function inspect() {
    if (!collection || busy) return;
    setBusy(true);
    setError(null);
    try {
      const inspection = await api.inspectCollection(collection, environment);
      onInspected({ collection, environment }, inspection);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card space-y-5 p-6">
      <div>
        <h2 className="text-lg font-semibold">Choose your files</h2>
        <p className="mt-1 text-sm text-slate-400">
          The collection is only read at this point — no request is sent until you confirm on the review step.
        </p>
      </div>
      <FilePicker
        id="collection-file"
        label="Collection file"
        hint="Postman Collection v2.0 or v2.1 JSON (max 10 MiB). Required."
        file={collection}
        onPick={setCollection}
      />
      <FilePicker
        id="environment-file"
        label="Environment file"
        hint="Postman environment JSON (max 2 MiB). Optional."
        file={environment}
        onPick={setEnvironment}
      />
      {error && (
        <p role="alert" className="rounded bg-danger/15 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      <button className="btn w-full justify-center" disabled={!collection || busy} onClick={inspect}>
        {busy ? "Inspecting…" : "Inspect collection"}
      </button>
    </div>
  );
}
