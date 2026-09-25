import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { discardSession } from "@/lib/session";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, deleteAnalysis: vi.fn(), deleteExecutionJob: vi.fn() } };
});
const m = vi.mocked(api);

beforeEach(() => {
  m.deleteAnalysis.mockReset();
  m.deleteExecutionJob.mockReset();
});

describe("discardSession", () => {
  it("deletes the analysis and its job", async () => {
    m.deleteAnalysis.mockResolvedValue(undefined);
    m.deleteExecutionJob.mockResolvedValue(undefined);
    await discardSession({ analysisId: "an_1", jobId: "job_1" });
    expect(m.deleteAnalysis).toHaveBeenCalledWith("an_1");
    expect(m.deleteExecutionJob).toHaveBeenCalledWith("job_1");
  });

  it("never throws when a delete fails, and skips missing ids", async () => {
    m.deleteAnalysis.mockRejectedValue(new Error("network"));
    await expect(discardSession({ analysisId: "an_1", jobId: null })).resolves.toBeUndefined();
    expect(m.deleteExecutionJob).not.toHaveBeenCalled();
  });
});
