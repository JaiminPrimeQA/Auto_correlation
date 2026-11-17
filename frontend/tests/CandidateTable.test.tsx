import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { CandidateTable } from "@/components/CandidateTable";
import { api, type Candidate } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    listCandidates: vi.fn(),
    acceptHigh: vi.fn(),
    acceptCandidate: vi.fn(),
    rejectCandidate: vi.fn(),
  },
}));

function candidate(id: string, variable: string, confidence: Candidate["confidence"] = "low"): Candidate {
  const occurrence = {
    execution_id: "e1",
    execution_index: 0,
    location_type: "json_body",
    canonical_path: `$.${variable}`,
  } as unknown as Candidate["producer"];
  return {
    id,
    variable_name: variable,
    producer: occurrence,
    consumers: [occurrence],
    consumer_count: 1,
    extractor_method: "json",
    extractor_expression: `$.${variable}`,
    match_number: 1,
    classification: "dynamic",
    confidence,
    evidence: { score: 1, factors: [], penalties: [], baseline_value_masked: "ab***", comparison_value_masked: null },
    warnings: [],
    state: "suggested",
  } as unknown as Candidate;
}

beforeEach(() => vi.resetAllMocks());

it("lists only open (suggested) candidates", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 0, items: [] });
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  await waitFor(() => expect(api.listCandidates).toHaveBeenCalledWith("example", { state: "suggested" }));
  expect(await screen.findByText(/No open candidates/)).toBeInTheDocument();
});

it("removes a candidate from the list as soon as it is accepted", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({
    total: 2,
    items: [candidate("c1", "merchantGuid"), candidate("c2", "token")],
  });
  vi.mocked(api.acceptCandidate).mockResolvedValue({} as never);
  const onRulesChanged = vi.fn();
  render(<CandidateTable analysisId="example" onRulesChanged={onRulesChanged} />);

  const row = (await screen.findByText("${merchantGuid}")).closest("div.rounded-\\[10px\\]") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Accept" }));

  await waitFor(() => expect(screen.queryByText("${merchantGuid}")).not.toBeInTheDocument());
  expect(screen.getByText("${token}")).toBeInTheDocument();
  expect(await screen.findByRole("status")).toHaveTextContent("Accepted ${merchantGuid}");
  expect(api.acceptCandidate).toHaveBeenCalledWith("example", "c1");
  expect(onRulesChanged).toHaveBeenCalled();
});

it("removes a rejected candidate from the list", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 1, items: [candidate("c1", "url")] });
  vi.mocked(api.rejectCandidate).mockResolvedValue({} as never);
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
  await waitFor(() => expect(screen.queryByText("${url}")).not.toBeInTheDocument());
});

it("puts the candidate back and shows the error when accepting fails", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 1, items: [candidate("c1", "tokenGuid")] });
  vi.mocked(api.acceptCandidate).mockRejectedValue(new Error("A consumer is already assigned"));
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("A consumer is already assigned");
  expect(screen.getByText("${tokenGuid}")).toBeInTheDocument();
});

it("shows an acceptance error instead of losing the rejected request", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 1, items: [candidate("c1", "sid", "high")] });
  vi.mocked(api.acceptHigh).mockRejectedValue(new Error("Run health blocks automatic correlation"));
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  const button = await screen.findByRole("button", { name: "Accept all high (1)" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Run health blocks automatic correlation");
});

it("disables Accept all high when no high-confidence candidates remain", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 1, items: [candidate("c1", "url")] });
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  await screen.findByText("${url}");
  expect(screen.getByRole("button", { name: "Accept all high" })).toBeDisabled();
});
