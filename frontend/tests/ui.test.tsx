import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Stepper } from "@/components/ui/Stepper";
import { Tabs } from "@/components/ui/Tabs";
import { ToastProvider, useToast } from "@/components/ui/Toast";

describe("Stepper", () => {
  it("marks done, current and upcoming steps", () => {
    render(<Stepper steps={["Files", "Variables", "Review", "Run"]} current={2} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((i) => i.dataset.state)).toEqual(["done", "done", "current", "todo"]);
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("list", { name: "Progress" })).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("selects a tab and reports changes", () => {
    const onChange = vi.fn();
    render(<Tabs tabs={[{ id: "a", label: "Run health" }, { id: "b", label: "Explorer" }]} active="a" onChange={onChange} />);
    expect(screen.getByRole("tab", { name: "Run health" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Explorer" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("Toast", () => {
  function Trigger() {
    const toast = useToast();
    return <button onClick={() => toast.show("2 correlations created")}>go</button>;
  }

  it("shows a status message and hides it again", () => {
    vi.useFakeTimers();
    render(<ToastProvider><Trigger /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByRole("status")).toHaveTextContent("2 correlations created");
    act(() => { vi.advanceTimersByTime(2500); });
    expect(screen.getByRole("status")).toHaveTextContent("");
    vi.useRealTimers();
  });
});
