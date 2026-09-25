import { afterEach, describe, expect, it, vi } from "vitest";
import { api, setTokenProvider } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
  setTokenProvider(null);
});

function stubFetch(response: () => Response) {
  const fetchMock = vi.fn(async () => response());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function headersOf(fetchMock: ReturnType<typeof vi.fn>, call = 0): Headers {
  return new Headers((fetchMock.mock.calls[call] as unknown as [string, RequestInit])[1].headers);
}

describe("bearer token", () => {
  it("is attached to JSON and multipart calls when a provider is set", async () => {
    setTokenProvider(async () => "tok-123");
    const fetchMock = stubFetch(() => new Response("{}", { status: 200 }));
    await api.getAnalysis("a1");
    await api.inspectCollection(new File(["{}"], "c.json"));
    expect(headersOf(fetchMock, 0).get("Authorization")).toBe("Bearer tok-123");
    expect(headersOf(fetchMock, 0).get("Content-Type")).toBe("application/json");
    expect(headersOf(fetchMock, 1).get("Authorization")).toBe("Bearer tok-123");
    expect(headersOf(fetchMock, 1).has("Content-Type")).toBe(false); // browser sets the multipart boundary
  });

  it("is omitted when there is no provider or no token", async () => {
    const fetchMock = stubFetch(() => new Response("{}", { status: 200 }));
    await api.getAnalysis("a1");
    setTokenProvider(async () => null);
    await api.getAnalysis("a1");
    expect(headersOf(fetchMock, 0).has("Authorization")).toBe(false);
    expect(headersOf(fetchMock, 1).has("Authorization")).toBe(false);
  });

  it("keeps the idempotency key alongside the token", async () => {
    setTokenProvider(async () => "tok");
    const fetchMock = stubFetch(() => new Response("{}", { status: 202 }));
    await api.createExecutionJob({
      collection: new File(["{}"], "c.json"), environment: null, folderId: null, suppliedValues: {},
      idempotencyKey: "k1",
    });
    expect(headersOf(fetchMock).get("Idempotency-Key")).toBe("k1");
    expect(headersOf(fetchMock).get("Authorization")).toBe("Bearer tok");
  });
});

describe("download", () => {
  it("fetches the file with the token and uses the server's filename", async () => {
    setTokenProvider(async () => "tok");
    const fetchMock = stubFetch(() =>
      new Response("<jmeterTestPlan/>", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="Checkout_flow.jmx"' },
      }),
    );
    const file = await api.download("a1", "jmx");
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/v1/analyses/a1/download/jmx");
    expect(headersOf(fetchMock).get("Authorization")).toBe("Bearer tok");
    expect(file.filename).toBe("Checkout_flow.jmx");
    expect(await file.blob.text()).toBe("<jmeterTestPlan/>");
  });

  it("falls back to a default filename", async () => {
    stubFetch(() => new Response("{}", { status: 200 }));
    expect((await api.download("a1", "manifest")).filename).toBe("manifest.json");
  });

  it("raises the problem detail on failure", async () => {
    stubFetch(() => new Response(JSON.stringify({ code: "not_found", detail: "Generate the JMX first." }), { status: 404 }));
    await expect(api.download("a1", "jmx")).rejects.toThrow("Generate the JMX first.");
  });
});
