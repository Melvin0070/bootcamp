// Thin typed fetch client. The UI is always same-origin with the API (Vite dev
// proxy locally, nginx proxy in compose), so the base is a relative "/api" and
// no CORS is involved. Every method's payload/return type is the generated
// OpenAPI type, so a contract change surfaces as a TypeScript error.
import type {
  ChatRequest,
  ChatResponse,
  DatasetRequest,
  DatasetResponse,
  EnrichmentRecord,
  ExcavateResponse,
  FossilDiffResponse,
  IngestRequest,
  IngestResult,
  MutateRequest,
  MutateResponse,
  SlideMutateRequest,
  SlideMutateResponse,
  TimeTravelResponse,
} from "./types";

export const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    // Network failure / API down → a uniform, surfaceable error.
    throw new ApiError(0, "Cannot reach the FossilRAG API. Is the stack up?");
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body?.detail !== undefined) detail = body.detail;
    } catch {
      // non-JSON error body → keep the status text
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Record<string, unknown>>("/healthz"),

  excavate: (q: string, k = 5, sourceId?: string) =>
    request<ExcavateResponse>(`/excavate${qs({ q, k, source_id: sourceId })}`),

  ingest: (body: IngestRequest) =>
    request<IngestResult>("/ingest", { method: "POST", body: JSON.stringify(body) }),

  mutate: (body: MutateRequest) =>
    request<MutateResponse>("/mutate", { method: "POST", body: JSON.stringify(body) }),

  chat: (body: ChatRequest) =>
    request<ChatResponse>("/chat", { method: "POST", body: JSON.stringify(body) }),

  timetravel: (sourceId: string, version?: number) =>
    request<TimeTravelResponse>(`/timetravel${qs({ source_id: sourceId, version })}`),

  diff: (sourceId: string, fromVersion: number, toVersion: number) =>
    request<FossilDiffResponse>(
      `/diff${qs({ source_id: sourceId, from_version: fromVersion, to_version: toVersion })}`,
    ),

  enrich: (body: IngestRequest) =>
    request<EnrichmentRecord>("/enrich", { method: "POST", body: JSON.stringify(body) }),

  slideMutate: (body: SlideMutateRequest) =>
    request<SlideMutateResponse>("/slide/mutate", { method: "POST", body: JSON.stringify(body) }),

  dataset: (body: DatasetRequest) =>
    request<DatasetResponse>("/dataset", { method: "POST", body: JSON.stringify(body) }),
};

export type Api = typeof api;
