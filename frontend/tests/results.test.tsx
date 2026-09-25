import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NextStepCard } from "@/components/results/NextStepCard";
import { ResultsHeader } from "@/components/results/ResultsHeader";
import { HelpNote } from "@/components/HelpNote";
import { makeSummary } from "./fixtures";

const summary = makeSummary({
  collection_name: "Booking Flow",
  readiness: { state: "ready", reasons: [], blockers: [], scenario_warnings: [] },
  auto_correlation_status: "not_started", jmx_status: null, rule_count: 0,
  summary: { correlations: 2, parameterizations: 0, external_credentials: 0, cookie_managed: 0, noise: 5, review_required: 0 },
});

describe("ResultsHeader", () => {
  it("shows the collection, readiness and six counts", () => {
    render(<ResultsHeader summary={summary} />);
    expect(screen.getByRole("heading", { name: "Booking Flow" })).toBeInTheDocument();
    expect(screen.getByText(/ready/i)).toBeInTheDocument();
    for (const label of ["Correlations", "Parameters", "Credentials", "Cookies", "Noise", "Needs review"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("reassures when there are no confirmed correlations", () => {
    render(<ResultsHeader summary={makeSummary({ ...summary, summary: { ...summary.summary, correlations: 0 } })} />);
    expect(screen.getByText(/nothing was fabricated/i)).toBeInTheDocument();
  });

  it("does not show the reassurance note when correlations were found", () => {
    render(<ResultsHeader summary={summary} />);
    expect(screen.queryByText(/nothing was fabricated/i)).not.toBeInTheDocument();
  });
});

describe("NextStepCard", () => {
  it("offers auto-correlation first", () => {
    const onAuto = vi.fn();
    render(<NextStepCard summary={summary} busy={false} onAutoCorrelate={onAuto} onOpenTab={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Auto-correlate" }));
    expect(onAuto).toHaveBeenCalledOnce();
  });

  it("then sends the tester to Generate", () => {
    const onOpen = vi.fn();
    render(<NextStepCard summary={makeSummary({ ...summary, auto_correlation_status: "completed", rule_count: 2 })}
      busy={false} onAutoCorrelate={() => {}} onOpenTab={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Generate" }));
    expect(onOpen).toHaveBeenCalledWith("generate");
  });
});

describe("HelpNote", () => {
  it("is collapsed by default behind 'What is this page?'", () => {
    render(<HelpNote title="Run health" steps={["It checks both runs."]} />);
    const details = screen.getByText(/what is this page\?/i).closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("It checks both runs.")).not.toBeVisible();
  });
});
