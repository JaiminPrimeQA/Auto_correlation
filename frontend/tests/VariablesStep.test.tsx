import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { VariablesStep } from "@/components/collection/VariablesStep";
import { makeInspection } from "./fixtures";

function Harness({ onContinue = () => {}, initial = {} }: { onContinue?: () => void; initial?: Record<string, string> }) {
  const [values, setValues] = useState<Record<string, string>>(initial);
  return (
    <VariablesStep
      inspection={makeInspection()}
      values={values}
      onChange={setValues}
      onBack={() => {}}
      onContinue={onContinue}
    />
  );
}

describe("VariablesStep", () => {
  it("asks for each unresolved variable, masking sensitive ones", () => {
    render(<Harness />);
    expect(screen.getByLabelText("api_key")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("host")).toHaveAttribute("type", "text");
    expect(screen.queryByLabelText("token")).toBeNull();
  });

  it("explains variables that are not asked for", () => {
    render(<Harness />);
    expect(screen.getByText("token").closest("li")).toHaveTextContent(/set at runtime by a script/i);
    expect(screen.getByText("region").closest("li")).toHaveTextContent(/environment/i);
  });

  it("blocks continuing until every required value is filled", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    const next = screen.getByRole("button", { name: /continue/i });
    expect(next).toBeDisabled();
    fireEvent.change(screen.getByLabelText("host"), { target: { value: "api.example.com" } });
    expect(next).toBeDisabled();
    fireEvent.change(screen.getByLabelText("api_key"), { target: { value: "k-123" } });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("with nothing to supply, says so and allows continuing", () => {
    render(
      <VariablesStep
        inspection={makeInspection({ unresolved_variable_names: [], variables: [] })}
        values={{}}
        onChange={() => {}}
        onBack={() => {}}
        onContinue={() => {}}
      />,
    );
    expect(screen.getByText(/no values needed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continue/i })).toBeEnabled();
  });
});
