import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PrivacyNote } from "@/components/PrivacyNote";

describe("PrivacyNote", () => {
  it("states the promise and reveals the four facts on demand", () => {
    render(<PrivacyNote mode="local" />);
    expect(screen.getByText(/processed in memory, never stored, and deleted after 30 minutes/i)).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /how we handle your data/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const fact of [
      /held in memory only\. there is no database/i,
      /temporary workspace is deleted as soon as the run ends/i,
      /results expire after 30 minutes\. "new analysis" deletes them immediately/i,
      /never logged, and never written into the jmx/i,
    ]) {
      expect(screen.getByText(fact)).toBeVisible();
    }
  });

  it("never claims 'no database' for the AWS deployment", () => {
    render(<PrivacyNote mode="aws" />);
    fireEvent.click(screen.getByRole("button", { name: /how we handle your data/i }));
    expect(document.body.textContent).not.toMatch(/no database/i);
    expect(screen.getByText(/stored encrypted and deleted after 30 minutes/i)).toBeInTheDocument();
  });
});
