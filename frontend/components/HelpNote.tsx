"use client";

// A small, collapsible "how this works" panel shown at the top of each page.
// Plain-language guidance so first-time users always know what to do next.

export function HelpNote({
  title,
  steps,
  tip,
}: {
  title: string;
  steps: string[];
  tip?: string;
}) {
  return (
    <details className="card mb-4 p-3 text-sm" open>
      <summary className="cursor-pointer select-none font-medium text-brand">
        💡 {title}
      </summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-slate-300">
        {steps.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ol>
      {tip && (
        <p className="mt-2 rounded bg-ink/50 px-3 py-2 text-xs text-slate-400">
          Tip: {tip}
        </p>
      )}
    </details>
  );
}
