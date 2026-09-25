import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async () =>
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonFile(name: string, content: unknown = {}) {
  return new File([JSON.stringify(content)], name, { type: "application/json" });
}

afterEach(() => vi.unstubAllGlobals());

describe("inspectCollection", () => {
  it("posts the collection and optional environment as multipart", async () => {
    const fetchMock = stubFetch(200, { collection_name: "C" });
    await api.inspectCollection(jsonFile("c.json"), jsonFile("e.json"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/execution-jobs/inspect");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect((form.get("collection") as File).name).toBe("c.json");
    expect((form.get("environment") as File).name).toBe("e.json");
  });

  it("omits the environment when none is given", async () => {
    const fetchMock = stubFetch(200, { collection_name: "C" });
    await api.inspectCollection(jsonFile("c.json"));
    const form = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(form.has("environment")).toBe(false);
  });
});

describe("createExecutionJob", () => {
  it("sends confirmation, folder, supplied values and the idempotency key", async () => {
    const fetchMock = stubFetch(202, { job_id: "j1", state: "queued" });
    const job = await api.createExecutionJob({
      collection: jsonFile("c.json"),
      environment: null,
      folderId: "auth",
      suppliedValues: { token: "s3cret" },
      idempotencyKey: "attempt-1",
    });
    expect(job.job_id).toBe("j1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/execution-jobs");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("attempt-1");
    const form = init.body as FormData;
    expect(form.get("confirm")).toBe("true");
    expect(form.get("folder_id")).toBe("auth");
    expect(JSON.parse(form.get("supplied_values_json") as string)).toEqual({ token: "s3cret" });
    expect(form.has("environment")).toBe(false);
  });

  it("does not send a folder when the whole collection runs", async () => {
    const fetchMock = stubFetch(202, { job_id: "j1", state: "queued" });
    await api.createExecutionJob({
      collection: jsonFile("c.json"), environment: null, folderId: null, suppliedValues: {}, idempotencyKey: "k",
    });
    const form = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(form.has("folder_id")).toBe(false);
  });

  it("maps a problem response to an ApiError with status, code and joined details", async () => {
    stubFetch(422, {
      code: "validation_error",
      detail: "Unresolved variables",
      errors: [{ detail: "host is missing" }, { detail: "token is missing" }],
    });
    const error = await api
      .createExecutionJob({
        collection: jsonFile("c.json"), environment: null, folderId: null, suppliedValues: {}, idempotencyKey: "k",
      })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).code).toBe("validation_error");
    expect((error as ApiError).message).toBe("Unresolved variables: host is missing; token is missing");
  });
});

describe("execution job status", () => {
  it("gets and deletes a job by id", async () => {
    const fetchMock = stubFetch(200, { job_id: "j/1", state: "running_baseline" });
    await api.getExecutionJob("j/1");
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/v1/execution-jobs/j%2F1");
    stubFetch(204, undefined);
    await expect(api.deleteExecutionJob("j1")).resolves.toBeUndefined();
  });

  it("a non-JSON error body still becomes an ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad gateway", { status: 502 })));
    const error = await api.getExecutionJob("j1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
  });
});
