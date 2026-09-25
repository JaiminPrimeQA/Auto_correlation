import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModeChooser } from "@/components/ModeChooser";

describe("ModeChooser", () => {
  it("offers the collection mode first, marked recommended", () => {
    render(<ModeChooser onChoose={() => {}} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveTextContent(/run a postman collection/i);
    expect(buttons[0]).toHaveTextContent(/recommended/i);
    expect(buttons[1]).toHaveTextContent(/upload existing newman reports/i);
  });

  it("reports the chosen mode", () => {
    const onChoose = vi.fn();
    render(<ModeChooser onChoose={onChoose} />);
    fireEvent.click(screen.getByRole("button", { name: /run a postman collection/i }));
    fireEvent.click(screen.getByRole("button", { name: /upload existing newman reports/i }));
    expect(onChoose.mock.calls).toEqual([["collection"], ["reports"]]);
  });
});
