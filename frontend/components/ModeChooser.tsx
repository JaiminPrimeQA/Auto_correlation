"use client";

// First screen: pick how the two runs get here (spec §7). Running the
// collection is recommended; uploading existing Newman reports stays available.

export type InputMode = "collection" | "reports";

export function ModeChooser({ onChoose }: { onChoose: (mode: InputMode) => void }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Start a correlation analysis</h1>
        <p className="mt-1 text-sm text-slate-400">
          The tool needs two successful runs of the same collection. Let it run your collection for you, or bring
          two Newman reports you already captured.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <button
          type="button"
          onClick={() => onChoose("collection")}
          className="card flex flex-col items-start gap-2 border-brand/60 p-5 text-left transition hover:bg-brand/5"
        >
          <span className="badge badge-high">Recommended</span>
          <span className="text-base font-semibold">Run a Postman collection</span>
          <span className="text-sm text-slate-400">
            Upload a collection (and optional environment). The tool runs it twice in isolated Newman containers
            and analyses both runs.
          </span>
        </button>
        <button
          type="button"
          onClick={() => onChoose("reports")}
          className="card flex flex-col items-start gap-2 p-5 text-left transition hover:bg-edge/40"
        >
          <span className="badge badge-low">Advanced</span>
          <span className="text-base font-semibold">Upload existing Newman reports</span>
          <span className="text-sm text-slate-400">
            Already ran the collection twice with <code className="mono">newman -r json</code>? Upload the
            baseline and comparison reports.
          </span>
        </button>
      </div>
    </div>
  );
}
