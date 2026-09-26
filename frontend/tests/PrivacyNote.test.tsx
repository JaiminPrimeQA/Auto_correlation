import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PrivacyNote } from "@/components/PrivacyNote";

describe("PrivacyNote", () => {
  it("states the promise and reveals the four facts on demand", () => {
    render(<PrivacyNote mode="local" />);
    expect(screen.getByText(/local processing uses memory and temporary files/i)).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /how we handle your data/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const fact of [
      /collection runs also write inputs, credentials and reports to a temporary workspace/i,
      /temporary workspace is deleted as soon as the run ends/i,
      /results expire after the configured session lifetime/i,
      /embed static secrets/i,
    ]) {
      expect(screen.getByText(fact)).toBeVisible();
    }
  });

  it("never claims 'no database' for the AWS deployment", () => {
    render(<PrivacyNote mode="aws" />);
    fireEvent.click(screen.getByRole("button", { name: /how we handle your data/i }));
    expect(document.body.textContent).not.toMatch(/no database/i);
    expect(screen.getByText(/session expiry and physical file deletion follow different schedules/i)).toBeInTheDocument();
    expect(screen.getByText(/asynchronous deletion/i)).toBeVisible();
  });
});
