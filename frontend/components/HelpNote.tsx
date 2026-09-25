"use client";

// A small, collapsible "how this works" panel shown at the top of each page.
// Plain-language guidance so first-time users always know what to do next.

import { QuestionIcon } from "@phosphor-icons/react";

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
    <details className="group my-3 text-sm">
      <summary className="focus-ring inline-flex cursor-pointer list-none items-center gap-1.5 rounded text-[13px] font-medium text-accent-soft-ink [&::-webkit-details-marker]:hidden">
        <QuestionIcon size={14} aria-hidden />
        What is this page? <span className="font-normal text-fg-subtle">{title}</span>
      </summary>
      <div className="card mt-2 p-4">
        <ol className="list-decimal space-y-1 pl-5 text-fg-muted">
          {steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
        {tip && (
          <p className="mt-3 rounded-[10px] bg-surface2 px-3 py-2 text-[13px] text-fg-muted">
            Tip: {tip}
          </p>
        )}
      </div>
    </details>
  );
}
