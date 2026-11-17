import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, type HarFileReview } from "@/lib/api";
import { HarImport } from "@/components/collection/HarImport";
import { jsonFile, makeSummary } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, inspectHar: vi.fn(), createHarAnalysis: vi.fn(), autoCorrelate: vi.fn(), getAnalysis: vi.fn() },
  };
});

const m = vi.mocked(api);

function review(overrides: Partial<HarFileReview> = {}): HarFileReview {
  return {
    filename: "flow.har",
    total: 5,
    kept: 2,
    optional: 2,
    removed: 1,
    size_bytes: 30_000_000,
    cleaned_bytes: 2_000,
    removed_by_reason: { JavaScript: 1 },
    warnings: ["This recording has no cookies."],
    entries: [
      { index: 1, method: "POST", url: "https://api.test/login", status: 200, decision: "keep", reason: "API call" },
      { index: 2, method: "GET", url: "https://api.test/me", status: 200, decision: "keep", reason: "API call" },
      { index: 3, method: "POST", url: "https://api.test/login", status: 200, decision: "optional", reason: "Failed attempt: isLoginSuccessful is false" },
      { index: 4, method: "GET", url: "https://chat.vendor.io/x", status: 200, decision: "optional", reason: "Third-party service" },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  onDone.mockReset();
  for (const fn of [m.inspectHar, m.createHarAnalysis, m.autoCorrelate, m.getAnalysis]) fn.mockReset();
  m.inspectHar.mockResolvedValue({ files: [review()] });
});

async function upload() {
  render(<HarImport onDone={onDone} />);
  fireEvent.change(screen.getByLabelText(/har recording/i), { target: { files: [recordingFile] } });
  fireEvent.click(screen.getByRole("button", { name: /clean recording/i }));
  await screen.findByRole("heading", { name: /check what goes into your plan/i });
}

const recordingFile = jsonFile("download 1");
const onDone = vi.fn();

describe("HarImport", () => {
  it("locks the chosen recording while inspection is running", async () => {
    let finish!: (value: { files: HarFileReview[] }) => void;
    m.inspectHar.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    m.createHarAnalysis.mockResolvedValue(makeSummary({ blocked: false }));
    m.autoCorrelate.mockResolvedValue({ total: 0, variables: [] } as never);
    m.getAnalysis.mockResolvedValue(makeSummary({ blocked: false }));
    render(<HarImport onDone={onDone} />);
    const input = screen.getByLabelText(/har recording/i);
    fireEvent.change(input, { target: { files: [recordingFile] } });
    fireEvent.click(screen.getByRole("button", { name: /clean recording/i }));
    expect(input).toBeDisabled();
    expect(screen.getByLabelText(/second recording/i)).toBeDisabled();
    fireEvent.drop(input.closest("label")!, { dataTransfer: { files: [jsonFile("different.har")] } });
    finish({ files: [review()] });
    await screen.findByRole("heading", { name: /check what goes into your plan/i });
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));
    await waitFor(() => expect(m.createHarAnalysis).toHaveBeenCalledWith([recordingFile], [1, 2]));
  });

  it("filters the review without losing selections hidden by search", async () => {
    m.createHarAnalysis.mockResolvedValue(makeSummary({ blocked: false }));
    m.autoCorrelate.mockResolvedValue({ total: 0, variables: [] } as never);
    m.getAnalysis.mockResolvedValue(makeSummary({ blocked: false }));
    await upload();
    fireEvent.change(screen.getByRole("searchbox", { name: /search requests/i }), { target: { value: "chat.vendor" } });
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));
    await waitFor(() => expect(m.createHarAnalysis).toHaveBeenCalledWith([recordingFile], [1, 2, 4]));
  });

  it("cannot start until a recording is chosen", () => {
    render(<HarImport onDone={onDone} />);
    expect(screen.getByRole("button", { name: /clean recording/i })).toBeDisabled();
  });

  it("accepts a file with no extension", () => {
    render(<HarImport onDone={onDone} />);
    expect(screen.getByLabelText(/har recording/i)).not.toHaveAttribute("accept");
  });

  it("ticks real API calls and leaves failed and third-party calls unticked", async () => {
    await upload();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((b) => (b as HTMLInputElement).checked)).toEqual([true, true, false, false]);
    expect(screen.getByText(/isLoginSuccessful is false/)).toBeVisible();
    expect(screen.getByText("This recording has no cookies.")).toBeVisible();
    expect(screen.getByText("JavaScript: 1")).toBeInTheDocument();
  });

  it("converts the ticked requests, correlates, and hands on the fresh summary", async () => {
    const created = makeSummary({ blocked: false });
    const finished = makeSummary({ blocked: false, auto_correlation_status: "completed" });
    m.createHarAnalysis.mockResolvedValue(created);
    m.autoCorrelate.mockResolvedValue({ total: 2, variables: ["token", "tokenGuid"] } as never);
    m.getAnalysis.mockResolvedValue(finished);
    await upload();

    fireEvent.click(screen.getAllByRole("checkbox")[3]);
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(finished, "2 value(s) correlated: token, tokenGuid."));
    expect(m.createHarAnalysis).toHaveBeenCalledWith([recordingFile], [1, 4]);
    expect(m.autoCorrelate).toHaveBeenCalledWith("an_1");
  });

  it("opens the analysis instead of offering a paid retry when correlation fails", async () => {
    const created = makeSummary({ blocked: false });
    m.createHarAnalysis.mockResolvedValue(created);
    m.autoCorrelate.mockRejectedValue(new Error("network"));
    await upload();
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(created, expect.stringMatching(/did not finish/)));
    expect(m.createHarAnalysis).toHaveBeenCalledTimes(1);
  });

  it("does not auto-correlate a blocked recording", async () => {
    const blocked = makeSummary({ blocked: true });
    m.createHarAnalysis.mockResolvedValue(blocked);
    m.getAnalysis.mockResolvedValue(blocked);
    await upload();
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(blocked, expect.stringMatching(/blocked/)));
    expect(m.autoCorrelate).not.toHaveBeenCalled();
  });

  it("says so when nothing was correlated", async () => {
    const created = makeSummary({ blocked: false });
    m.createHarAnalysis.mockResolvedValue(created);
    m.autoCorrelate.mockResolvedValue({ total: 0, variables: [] } as never);
    m.getAnalysis.mockResolvedValue(created);
    await upload();
    fireEvent.click(screen.getByRole("button", { name: /convert to correlated jmx/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(created, expect.stringMatching(/no correlations/)));
  });

  it("cannot convert with nothing selected", async () => {
    await upload();
    for (const box of screen.getAllByRole("checkbox").slice(0, 2)) fireEvent.click(box);
    expect(screen.getByRole("button", { name: /convert to correlated jmx/i })).toBeDisabled();
  });

  it("shows why a file was refused", async () => {
    m.inspectHar.mockRejectedValue(new ApiError("'x' is not a HAR recording", 422, "validation_error"));
    render(<HarImport onDone={onDone} />);
    fireEvent.change(screen.getByLabelText(/har recording/i), { target: { files: [recordingFile] } });
    fireEvent.click(screen.getByRole("button", { name: /clean recording/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a HAR recording");
  });
});
