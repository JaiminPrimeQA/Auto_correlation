"use client";

import { useState } from "react";
import { FileArrowUpIcon } from "@phosphor-icons/react";
import { api, type CollectionInspection } from "@/lib/api";
import { PrivacyNote } from "@/components/PrivacyNote";
import { SampleWalkthrough } from "./SampleWalkthrough";

export interface CollectionFiles {
  collection: File;
  environment: File | null;
}

function DropZone({
  id, label, optional, emptyTitle, emptyHint, file, onPick,
}: {
  id: string; label: string; optional?: boolean; emptyTitle: string; emptyHint: string;
  file: File | null; onPick: (file: File | null) => void;
}) {
  return (
    <div>
      <span className="mb-2 block text-[13px] font-medium">
        {label} {optional && <span className="font-normal text-fg-subtle">(optional)</span>}
      </span>
      <label
        htmlFor={id}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const dropped = event.dataTransfer.files[0];
          if (dropped) onPick(dropped);
        }}
        className={`focus-within:ring-accent/30 flex cursor-pointer items-center gap-3.5 rounded-[10px] border-[1.5px] p-4 transition-colors duration-150 focus-within:ring-[3px] ${
          file ? "border-solid border-accent/40 bg-accent-soft/40" : "border-dashed border-line-strong bg-surface2 hover:border-accent"
        }`}
      >
        <span
          className={`grid h-10 w-10 flex-none place-items-center rounded-[10px] transition-transform duration-150 ${
            file ? "scale-[1.04] bg-accent text-accent-ink" : "bg-accent-soft text-accent-soft-ink"
          }`}
        >
          <FileArrowUpIcon size={20} aria-hidden />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{file ? file.name : emptyTitle}</span>
          <span className="block text-[13px] text-fg-subtle">
            {file ? `${(file.size / 1024).toFixed(1)} KiB` : emptyHint}
          </span>
        </span>
        <input
          id={id}
          aria-label={label}
          type="file"
          accept="application/json,.json"
          className="sr-only"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
      </label>
    </div>
  );
}

const HOW = [
  ["Upload and check", "We read the collection and list any values we still need."],
  ["Run twice, safely", "Two fresh Newman runs in locked-down containers."],
  ["Correlate and export", "Review what changed, then download a JMeter 5.6.3 plan."],
] as const;

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
    <>
    <div className="grid items-start gap-10 lg:grid-cols-[1fr_1.05fr]">
      <div className="rise">
        <h1 style={{ "--i": 0 } as React.CSSProperties} className="mb-3 max-w-[16ch] text-[32px] font-semibold leading-[1.12] tracking-[-0.03em]">
          Turn a Postman collection into a correlated JMeter plan
        </h1>
        <p style={{ "--i": 1 } as React.CSSProperties} className="mb-6 max-w-[44ch] text-[15px] leading-relaxed text-fg-muted">
          Upload your collection. We run it twice in an isolated sandbox, find the values that change between runs,
          and wire them into a test plan you can review, supply credentials for, and validate.
        </p>
        <ol style={{ "--i": 2 } as React.CSSProperties} className="space-y-3.5">
          {HOW.map(([title, text], i) => (
            <li key={title} className="flex gap-3 text-sm leading-normal text-fg-muted">
              <span className="grid h-7 w-7 flex-none place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent-soft-ink">{i + 1}</span>
              <span><b className="block font-medium text-fg">{title}</b>{text}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="card rise space-y-3 p-5">
        <div style={{ "--i": 1 } as React.CSSProperties}>
          <DropZone id="collection-file" label="Collection file" emptyTitle="Choose or drop a collection"
            emptyHint="Postman v2.0 or v2.1 JSON, up to 10 MiB" file={collection} onPick={setCollection} />
        </div>
        <div style={{ "--i": 2 } as React.CSSProperties}>
          <DropZone id="environment-file" label="Environment file" optional emptyTitle="Choose or drop an environment"
            emptyHint="Values such as baseUrl and password, up to 2 MiB" file={environment} onPick={setEnvironment} />
        </div>
        <div style={{ "--i": 3 } as React.CSSProperties}>
          <PrivacyNote />
        </div>
        {error && (
          <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
        )}
        <div style={{ "--i": 4 } as React.CSSProperties} className="flex items-center justify-between gap-3 pt-2">
          <span className="text-[12.5px] text-fg-subtle">Nothing runs until you confirm on step 3.</span>
          <button className="btn" disabled={!collection || busy} onClick={inspect}>
            {busy ? "Inspecting..." : "Inspect collection"}
          </button>
        </div>
      </div>
    </div>
    <SampleWalkthrough />
    </>
  );
}
