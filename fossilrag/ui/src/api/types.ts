// Domain types, aliased from the auto-generated OpenAPI schema so the UI's
// request/response shapes are provably in sync with the FastAPI Pydantic
// models. Regenerate schema.ts with `bun run gen:api` after the API changes.
import type { components } from "./schema";

type S = components["schemas"];

export type ExcavateHit = S["ExcavateHit"];
export type ExcavateResponse = S["ExcavateResponse"];
export type IngestRequest = S["IngestRequest"];
export type IngestResult = S["IngestResult"];
export type MutateRequest = S["MutateRequest"];
export type MutateResponse = S["MutateResponse"];
export type ChatMessage = S["ChatMessage"];
export type ChatRequest = S["ChatRequest"];
export type ChatResponse = S["ChatResponse"];
export type TimeTravelResponse = S["TimeTravelResponse"];
export type FossilDiffResponse = S["FossilDiffResponse"];
export type FossilLayerChunk = S["FossilLayerChunk"];
export type SlideMutateRequest = S["SlideMutateRequest"];
export type SlideMutateResponse = S["SlideMutateResponse"];
export type DatasetRequest = S["DatasetRequest"];
export type DatasetResponse = S["DatasetResponse"];
export type EnrichmentRecord = S["EnrichmentRecord"];
export type Marker = S["Marker"];
