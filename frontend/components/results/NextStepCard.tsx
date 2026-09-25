"use client";

import { CaretRightIcon } from "@phosphor-icons/react";
import type { AnalysisSummary } from "@/lib/api";
import { nextStep } from "@/lib/nextStep";

const JOURNEY = ["Auto-correlate", "Generate JMX", "Validate with JMeter"];

export function NextStepCard({
  summary, busy, onAutoCorrelate, onOpenTab,
}: {
  summary: AnalysisSummary; busy: boolean; onAutoCorrelate: () => void; onOpenTab: (tab: "generate") => void;
}) {
  const step = nextStep(summary);
  if (step === "blocked") return null;
  const index = step === "auto_correlate" ? 0 : step === "generate" ? 1 : 2;
  const content = {
    auto_correlate: {
      title: `Next: auto-correlate the ${summary.summary.correlations} reused value${summary.summary.correlations === 1 ? "" : "s"}`,
      text: "Adds an extractor and a variable for each value, and substitutes it everywhere it is reused.",
      action: <button className="btn" disabled={busy} onClick={onAutoCorrelate}>{busy ? "Correlating..." : "Auto-correlate"}</button>,
    },
    generate: {
      title: "Next: generate the JMeter plan",
      text: "Builds a JMeter 5.6.3 test plan with every accepted correlation wired in.",
      action: <button className="btn" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
    validate: {
      title: "Next: prove it works in JMeter 5.6.3",
      text: "Runs the generated plan once and checks every request and extracted variable.",
      action: <button className="btn" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
    done: {
      title: "Done: your plan is validated",
      text: "Download it from the Generate tab and run it in JMeter.",
      action: <button className="btn-secondary" onClick={() => onOpenTab("generate")}>Open Generate</button>,
    },
  }[step];

  return (
    <div className="card mb-4 flex flex-wrap items-center justify-between gap-4 border-accent/30 p-4">
      <div>
        <p className="text-[15px] font-semibold">{content.title}</p>
        <p className="mt-0.5 text-[13.5px] text-fg-muted">{content.text}</p>
        <ol aria-label="Steps" className="mt-2 flex items-center gap-1.5 text-[12.5px] text-fg-subtle">
          {JOURNEY.map((j, i) => (
            <li key={j} className="flex items-center gap-1.5">
              <span className={i === index && step !== "done" ? "font-medium text-accent-soft-ink" : i < index || step === "done" ? "text-fg-muted line-through decoration-line-strong" : ""}>{j}</span>
              {i < JOURNEY.length - 1 && <CaretRightIcon size={10} aria-hidden />}
            </li>
          ))}
        </ol>
      </div>
      {content.action}
    </div>
  );
}
