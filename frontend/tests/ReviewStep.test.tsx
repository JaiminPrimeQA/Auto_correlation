import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReviewStep } from "@/components/collection/ReviewStep";
import { jsonFile, makeInspection } from "./fixtures";

function renderReview(overrides: Partial<Parameters<typeof ReviewStep>[0]> = {}) {
  const props = {
    files: { collection: jsonFile("flow.json"), environment: jsonFile("dev.json") },
    inspection: makeInspection(),
    folderId: null,
    onFolderChange: vi.fn(),
    suppliedNames: ["api_key", "host"],
    error: null,
    onBack: vi.fn(),
    onSubmit: vi.fn(async () => {}),
    ...overrides,
  };
  render(<ReviewStep {...props} />);
  return props;
}

describe("ReviewStep", () => {
  it("summarises what will run", () => {
    renderReview();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Checkout flow");
    expect(text).toContain("flow.json");
    expect(text).toContain("dev.json");
    expect(text).toContain("api.example.com");
    expect(text).toMatch(/5 requests/i);
    expect(text).toMatch(/2 runs/i);
    expect(text).toMatch(/5 min/i);
  });

  it("lists supplied variable names but never their values", () => {
    renderReview();
    expect(screen.getByText("api_key")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("k-123");
  });

  it("shows the request count of the chosen folder and reports folder changes", () => {
    const props = renderReview({ folderId: "orders" });
    expect(document.body.textContent).toMatch(/3 requests/i);
    fireEvent.click(screen.getByRole("radio", { name: /^auth/i }));
    expect(props.onFolderChange).toHaveBeenCalledWith("auth");
    fireEvent.click(screen.getByRole("radio", { name: /whole collection/i }));
    expect(props.onFolderChange).toHaveBeenLastCalledWith(null);
  });

  it("surfaces domain warnings and unsupported features", () => {
    renderReview({
      inspection: makeInspection({
        domain_warnings: ["'Login': destination is a private address"],
        unsupported_features: [{ kind: "local_data_file", detail: "Uses a local file upload", location: "Upload" }],
      }),
    });
    expect(document.body.textContent).toContain("destination is a private address");
    expect(document.body.textContent).toContain("Uses a local file upload");
  });

  it("submits once even when clicked repeatedly", async () => {
    let finish: () => void = () => {};
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    renderReview({ onSubmit });
    const run = screen.getByRole("button", { name: /run collection twice/i });
    fireEvent.click(run);
    fireEvent.click(run);
    fireEvent.click(run);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(run).toBeDisabled();
    finish();
    await waitFor(() => expect(run).toBeEnabled());
  });

  it("shows a submission error", () => {
    renderReview({ error: "Too many active jobs" });
    expect(screen.getByRole("alert")).toHaveTextContent("Too many active jobs");
  });
});
