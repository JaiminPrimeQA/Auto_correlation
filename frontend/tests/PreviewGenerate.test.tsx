import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PreviewGenerate } from "@/components/PreviewGenerate";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { generate: vi.fn(), preview: vi.fn(), validate: vi.fn(), download: vi.fn() } }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.generate).mockResolvedValue({
    validation: { ok: true }, manifest: { summary: {}, secret_handling: { required_properties: ["Authorization"] } },
  });
});

describe("PreviewGenerate options", () => {
  it("keeps the browser's User-Agent for a recording", () => {
    render(<PreviewGenerate analysisId="demo" ruleCount={1} browserRecording />);
    expect(screen.getByRole("checkbox", { name: /user.agent/i })).toBeChecked();
  });

  it("leaves it off for a collection", () => {
    render(<PreviewGenerate analysisId="demo" ruleCount={1} />);
    expect(screen.getByRole("checkbox", { name: /user.agent/i })).not.toBeChecked();
  });
});

describe("JMeter guidance", () => {
  it("explains password setup and separates requests from correlations", async () => {
    vi.mocked(api.generate).mockResolvedValue({
      validation: { ok: true }, manifest: {
        summary: { matched_requests: 31, confirmed_correlations: 4, downstream_values_replaced: 56 },
        secret_handling: { required_properties: ["Code"] },
      },
    });
    render(<PreviewGenerate analysisId="demo" ruleCount={4} browserRecording />);
    fireEvent.click(screen.getByRole("button", { name: "Generate JMX" }));
    expect(await screen.findByRole("heading", { name: "Before running in JMeter" })).toBeVisible();
    expect(screen.getByText(/Code is your login password/)).toBeVisible();
    expect(screen.getByText(/without quotes or/)).toBeVisible();
    expect(screen.getByText("Requests in plan")).toBeVisible();
    expect(screen.getByText("Correlated values")).toBeVisible();
    expect(screen.getByText("31")).toBeVisible();
    expect(screen.getByText("56")).toBeVisible();
  });

  it("blocks generation for invalid thread and loop counts", () => {
    render(<PreviewGenerate analysisId="demo" ruleCount={1} />);
    fireEvent.change(screen.getByLabelText("threads"), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "Generate JMX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Preview Draft" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("threads"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("loops"), { target: { value: "1.5" } });
    expect(screen.getByRole("button", { name: "Generate JMX" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("loops"), { target: { value: "2" } });
    expect(screen.getByRole("button", { name: "Generate JMX" })).toBeEnabled();
  });

  it("shows download failures inline", async () => {
    vi.mocked(api.download).mockRejectedValue(new Error("Download unavailable"));
    render(<PreviewGenerate analysisId="demo" ruleCount={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate JMX" }));
    fireEvent.click(await screen.findByRole("button", { name: "Download Generated JMX" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Download unavailable");
  });

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
