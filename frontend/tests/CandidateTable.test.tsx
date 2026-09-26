import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CandidateTable } from "@/components/CandidateTable";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { listCandidates: vi.fn(), acceptHigh: vi.fn() } }));

it("shows an acceptance error instead of losing the rejected request", async () => {
  vi.mocked(api.listCandidates).mockResolvedValue({ total: 0, items: [] });
  vi.mocked(api.acceptHigh).mockRejectedValue(new Error("Run health blocks automatic correlation"));
  render(<CandidateTable analysisId="example" onRulesChanged={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Accept all high" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Accept all high" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Run health blocks automatic correlation");
});
