import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { ExecutionProgress } from "@/components/collection/ExecutionProgress";
import { makeJob, makeSummary } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, getExecutionJob: vi.fn(), deleteExecutionJob: vi.fn(), getAnalysis: vi.fn() },
  };
});

const getJob = vi.mocked(api.getExecutionJob);
const deleteJob = vi.mocked(api.deleteExecutionJob);
const getAnalysis = vi.mocked(api.getAnalysis);

beforeEach(() => {
  vi.useFakeTimers();
  getJob.mockReset();
  deleteJob.mockReset();
  getAnalysis.mockReset();
});
afterEach(() => vi.useRealTimers());

async function tick(ms = 1500) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function renderProgress(overrides: Partial<Parameters<typeof ExecutionProgress>[0]> = {}) {
  const props = { jobId: "job_1", onReady: vi.fn(), onRetry: vi.fn(), onStartOver: vi.fn(), ...overrides };
  render(<ExecutionProgress {...props} />);
  return props;
}

describe("ExecutionProgress", () => {
  it("names the current stage while the job runs", async () => {
    getJob.mockResolvedValue(makeJob({ state: "running_baseline", stage_history: ["validating", "running_baseline"] }));
    renderProgress();
    await tick(0);
    expect(screen.getByText(/run a/i).closest("li")).toHaveAttribute("data-status", "current");
    expect(screen.getByText(/checking every request destination/i).closest("li")).toHaveAttribute("data-status", "done");
  });

  it("keeps polling until ready, then opens the analysis", async () => {
    getJob
      .mockResolvedValueOnce(makeJob({ state: "running_comparison", stage_history: ["validating", "running_baseline", "running_comparison"] }))
      .mockResolvedValueOnce(makeJob({ state: "ready", analysis_id: "an_1", stage_history: ["validating", "running_baseline", "running_comparison", "analyzing", "ready"] }));
    const summary = makeSummary();
    getAnalysis.mockResolvedValue(summary);
    const props = renderProgress();
    await tick(0);
    expect(props.onReady).not.toHaveBeenCalled();
    await tick();
    await tick(0);
    expect(getAnalysis).toHaveBeenCalledWith("an_1");
    expect(props.onReady).toHaveBeenCalledWith(summary);
    const calls = getJob.mock.calls.length;
    await tick(5000);
    expect(getJob.mock.calls.length).toBe(calls);
  });

  it("stays on the page with the failure, guidance, and a retry action", async () => {
    getJob.mockResolvedValue(makeJob({
      state: "failed", stage_history: ["validating", "failed"],
      error_code: "destination_validation_failed",
      error_detail: "One or more request targets failed destination validation.",
      warnings: ["'Login': destination is a private address"],
    }));
    const props = renderProgress();
    await tick(0);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("failed destination validation");
    expect(alert).toHaveTextContent(/public https/i);
    expect(alert).toHaveTextContent("destination is a private address");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /start over/i }));
    expect(props.onStartOver).toHaveBeenCalledTimes(1);
  });

  it("cancels an active job", async () => {
    getJob
      .mockResolvedValueOnce(makeJob({ state: "running_baseline", stage_history: ["validating", "running_baseline"] }))
      .mockResolvedValue(makeJob({ state: "cancelled", stage_history: ["validating", "running_baseline", "cancelled"] }));
    deleteJob.mockResolvedValue(undefined);
    renderProgress();
    await tick(0);
    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));
    await tick(0);
    expect(deleteJob).toHaveBeenCalledWith("job_1");
    await tick();
    expect(screen.getByRole("alert")).toHaveTextContent(/cancelled/i);
    expect(screen.queryByRole("button", { name: /cancel run/i })).toBeNull();
  });

  it("treats a vanished job as expired", async () => {
    getJob.mockRejectedValue(new ApiError("Execution job not found or expired.", 404, "not_found"));
    renderProgress();
    await tick(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/not found or expired/i);
  });

  it("keeps polling through a transient network error", async () => {
    getJob
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(makeJob({ state: "validating", stage_history: ["validating"] }));
    renderProgress();
    await tick(0);
    await tick();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/checking every request destination/i).closest("li")).toHaveAttribute("data-status", "current");
  });
});
