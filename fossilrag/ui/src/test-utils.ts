import { vi } from "vitest";

interface MockResponse {
  body: unknown;
  ok?: boolean;
  status?: number;
}

/** Stub global fetch to resolve a single JSON response. */
export function mockFetchJson(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Stub global fetch to resolve a sequence of JSON responses (one per call). */
export function mockFetchSequence(...responses: MockResponse[]) {
  const fetchMock = vi.fn();
  for (const r of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: r.ok ?? true,
      status: r.status ?? 200,
      statusText: (r.ok ?? true) ? "OK" : "Error",
      json: async () => r.body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
