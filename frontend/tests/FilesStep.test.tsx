import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { FilesStep } from "@/components/collection/FilesStep";
import { jsonFile, makeInspection } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, inspectCollection: vi.fn() } };
});

const inspect = vi.mocked(api.inspectCollection);

beforeEach(() => {
  inspect.mockReset();
});

function choose(label: RegExp, file: File) {
  fireEvent.change(screen.getByLabelText(label), { target: { files: [file] } });
}

describe("FilesStep", () => {
  it("cannot inspect until a collection is chosen", () => {
    render(<FilesStep onInspected={() => {}} />);
    expect(screen.getByRole("button", { name: /how we handle your data/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /turn a postman collection into a correlated jmeter plan/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /inspect collection/i })).toBeDisabled();
  });

  it("inspects the collection with the optional environment and hands both on", async () => {
    const inspection = makeInspection();
    inspect.mockResolvedValue(inspection);
    const onInspected = vi.fn();
    render(<FilesStep onInspected={onInspected} />);
    const collection = jsonFile("flow.postman_collection.json");
    const environment = jsonFile("dev.postman_environment.json");
    choose(/collection file/i, collection);
    choose(/environment file/i, environment);
    fireEvent.click(screen.getByRole("button", { name: /inspect collection/i }));
    await waitFor(() => expect(onInspected).toHaveBeenCalled());
    expect(inspect).toHaveBeenCalledWith(collection, environment);
    expect(onInspected).toHaveBeenCalledWith({ collection, environment }, inspection);
  });

  it("shows the server's error and stays on the step", async () => {
    inspect.mockRejectedValue(new ApiError("Not a Postman collection", 422, "validation_error"));
    const onInspected = vi.fn();
    render(<FilesStep onInspected={onInspected} />);
    choose(/collection file/i, jsonFile("bad.json"));
    fireEvent.click(screen.getByRole("button", { name: /inspect collection/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not a Postman collection");
    expect(onInspected).not.toHaveBeenCalled();
  });
});
