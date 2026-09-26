import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PreviewGenerate } from "@/components/PreviewGenerate";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { generate: vi.fn(), preview: vi.fn(), validate: vi.fn() } }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.generate).mockResolvedValue({
    validation: { ok: true }, manifest: { summary: {}, secret_handling: { required_properties: ["Authorization"] } },
  });
});

describe("JMeter guidance", () => {
  it("requires credentials before validation and clears them afterwards", async () => {
    vi.mocked(api.validate).mockResolvedValue({ status: "validation_failed", report: {
      reasons: ["Credential expired"], sampler_results: [], variables_extracted: [], variables_missing: [],
    } });
    render(<PreviewGenerate analysisId="demo" ruleCount={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate JMX" }));
    const validate = await screen.findByRole("button", { name: "Validate with JMeter" });
    expect(validate).toBeDisabled();
    const input = screen.getByLabelText("Authorization");
    fireEvent.change(input, { target: { value: "Basic test-credential" } });
    expect(validate).toBeEnabled();
    fireEvent.click(validate);
    await waitFor(() => expect(api.validate).toHaveBeenCalledWith("demo", { Authorization: "Basic test-credential" }));
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("clears results when generation options change", async () => {
    render(<PreviewGenerate analysisId="demo" ruleCount={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate JMX" }));
    await screen.findByRole("button", { name: "Download Generated JMX" });
    fireEvent.change(screen.getByLabelText("threads"), { target: { value: "2" } });
    expect(screen.queryByRole("button", { name: "Download Generated JMX" })).not.toBeInTheDocument();
  });
});
