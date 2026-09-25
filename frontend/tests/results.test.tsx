import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NextStepCard } from "@/components/results/NextStepCard";
import { ResultsHeader } from "@/components/results/ResultsHeader";
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
