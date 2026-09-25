import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { CollectionWizard } from "@/components/collection/CollectionWizard";
import { jsonFile, makeInspection, makeJob, makeSummary } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      inspectCollection: vi.fn(),
      createExecutionJob: vi.fn(),
      getExecutionJob: vi.fn(),
      deleteExecutionJob: vi.fn(),
      getAnalysis: vi.fn(),
    },
  };
});

const m = vi.mocked(api);

beforeEach(() => {
  for (const fn of [m.inspectCollection, m.createExecutionJob, m.getExecutionJob, m.deleteExecutionJob, m.getAnalysis]) {
    fn.mockReset();
  }
  m.inspectCollection.mockResolvedValue(makeInspection());
});

async function throughToReview() {
  fireEvent.change(screen.getByLabelText(/collection file/i), { target: { files: [jsonFile("flow.json")] } });
  fireEvent.click(screen.getByRole("button", { name: /inspect collection/i }));
  await screen.findByLabelText("api_key");
  fireEvent.change(screen.getByLabelText("api_key"), { target: { value: "k-123" } });
  fireEvent.change(screen.getByLabelText("host"), { target: { value: "api.example.com" } });
  fireEvent.click(screen.getByRole("button", { name: /continue/i }));
  await screen.findByRole("button", { name: /run collection twice/i });
}

describe("CollectionWizard", () => {
  it("goes from files to results, sending the supplied values once", async () => {
    m.createExecutionJob.mockResolvedValue(makeJob());
    m.getExecutionJob.mockResolvedValue(makeJob({ state: "ready", analysis_id: "an_1", stage_history: ["ready"] }));
    const summary = makeSummary();
    m.getAnalysis.mockResolvedValue(summary);
    const onDone = vi.fn();
    render(<CollectionWizard onDone={onDone} onBack={() => {}} />);

    await throughToReview();
    fireEvent.click(screen.getByRole("radio", { name: /^auth/i }));
    fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(summary));
    expect(m.createExecutionJob).toHaveBeenCalledTimes(1);
    const input = m.createExecutionJob.mock.calls[0][0];
    expect(input.folderId).toBe("auth");
    expect(input.suppliedValues).toEqual({ api_key: "k-123", host: "api.example.com" });
    expect(input.idempotencyKey).toBeTruthy();
  });

  it("clears secrets after submitting; retry asks for them again with a new attempt key", async () => {
    m.createExecutionJob.mockResolvedValue(makeJob());
    m.getExecutionJob.mockResolvedValue(makeJob({
      state: "failed", stage_history: ["validating", "running_baseline", "failed"],
      error_code: "timeout", error_detail: "The Newman run exceeded its time limit and was stopped.",
    }));
    render(<CollectionWizard onDone={() => {}} onBack={() => {}} />);
    await throughToReview();
    fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));

    fireEvent.click(await screen.findByRole("button", { name: /retry/i }));
    expect(await screen.findByLabelText("api_key")).toHaveValue("");
    expect(screen.getByLabelText("host")).toHaveValue("api.example.com");

    fireEvent.change(screen.getByLabelText("api_key"), { target: { value: "k-456" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    fireEvent.click(await screen.findByRole("button", { name: /run collection twice/i }));
    await waitFor(() => expect(m.createExecutionJob).toHaveBeenCalledTimes(2));
    const [first, second] = m.createExecutionJob.mock.calls.map((c) => c[0]);
    expect(second.suppliedValues.api_key).toBe("k-456");
    expect(second.idempotencyKey).not.toBe(first.idempotencyKey);
  });

  it("a failed submission stays on review and reuses the same attempt key", async () => {
    m.createExecutionJob
      .mockRejectedValueOnce(new ApiError("Too many active jobs", 429, "too_many_active_jobs"))
      .mockResolvedValueOnce(makeJob());
    m.getExecutionJob.mockResolvedValue(makeJob({ state: "queued" }));
    render(<CollectionWizard onDone={() => {}} onBack={() => {}} />);
    await throughToReview();
    fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many active jobs");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));
    });
    await waitFor(() => expect(m.createExecutionJob).toHaveBeenCalledTimes(2));
    const [first, second] = m.createExecutionJob.mock.calls.map((c) => c[0]);
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("changing the folder starts a new attempt", async () => {
    m.createExecutionJob.mockRejectedValue(new ApiError("Server busy", 503, "runner_unavailable"));
    render(<CollectionWizard onDone={() => {}} onBack={() => {}} />);
    await throughToReview();
    fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("radio", { name: /^orders/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /run collection twice/i }));
    });
    await waitFor(() => expect(m.createExecutionJob).toHaveBeenCalledTimes(2));
    const [first, second] = m.createExecutionJob.mock.calls.map((c) => c[0]);
    expect(second.idempotencyKey).not.toBe(first.idempotencyKey);
  });

  it("shows which step the tester is on", async () => {
    render(<CollectionWizard onDone={() => {}} onBack={() => {}} />);
    const steps = () => within(screen.getByRole("list", { name: /wizard steps/i }));
    expect(steps().getByText("Files").closest("li")).toHaveAttribute("aria-current", "step");
    await throughToReview();
    expect(steps().getByText("Scope and review").closest("li")).toHaveAttribute("aria-current", "step");
    expect(steps().getByText("Files").closest("li")).not.toHaveAttribute("aria-current");
  });
});
