# React Fossil UI (Component 6 / mutation #7)

The **Excavation Console** — a single-page React app over the FossilRAG API. A
"stratigraphic core sample" field-guide aesthetic (Fraunces + Archivo + JetBrains
Mono, amber-on-sediment).

## Stack

| Concern | Tool |
|---------|------|
| Package manager + runtime | **Bun** (frozen lockfile everywhere: local, CI, Docker) |
| Bundler / dev server | **Vite** + `@vitejs/plugin-react` |
| Language | **React 19** + **TypeScript** (strict) |
| Lint + format | **Biome** (one fast tool, mirrors the Python side's ruff) |
| Tests | **Vitest** + jsdom + Testing Library |
| API types | **openapi-typescript** — generated from the app's OpenAPI |

## Tabs

Excavate · Mutate · Chat · Fossil Layers (time-travel + diff) · Slide Mutator ·
Dataset · Enrich · Ingest — covering all 12 API endpoints (every use case + the
mutations).

## Contract safety

`src/api/schema.ts` is **generated** from the API's OpenAPI, and the typed
client (`src/api/client.ts`) aliases those types — so an API contract change
surfaces as a TypeScript error, not a runtime surprise.

```bash
make -C .. ... # or, from this dir:
bun run gen:api   # regenerate types after the API changes
#   1. dump the spec:  (repo root) python -c "import json;from fossilrag.api.app import app;print(json.dumps(app.openapi()))" > ui/openapi.json
#   2. bun run gen:api
```

## No CORS, by design

The browser is always **same-origin** with the API: in dev, Vite proxies
`/api` → the FastAPI server; in the container, nginx proxies `/api` → the `api`
service (`nginx.conf`). So the API needs no CORS middleware. Point the dev proxy
elsewhere with `FOSSILRAG_API_URL`.

## Develop

```bash
bun install
bun run dev        # http://localhost:5173 (proxies /api -> http://localhost:8000)
bun run test       # render tests (Vitest + Testing Library)
bun run lint       # biome
bun run typecheck  # tsc --noEmit
bun run build      # tsc + vite build -> dist/
```

Or via the stack: `make up-ui` (postgres + api + UI at http://localhost:5173).

## Verification posture

- **Logic is CI-gated**: a dedicated `ui` CI job runs Biome + typecheck + the
  Vitest render suite + `vite build` on every PR. Each panel asserts its
  success **and** error/empty states (the white-screen class that `tsc`/build
  can't catch).
- **Visual QA is manual** (no browser in CI): run `bun run dev` / `make up-ui`
  and look. The render tests query by text/role/label, so they're independent
  of the styling.
