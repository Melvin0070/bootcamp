# FossilRAG — Demo Script

A ~6-minute walkthrough touching all three use cases, the headline mutations,
the UI, and the emulated serverless ingestion loop. Everything here runs at $0.

## 0. Bring up the stack

```bash
cd fossilrag
make up-ui            # postgres(pgvector) + API + the React UI (nginx)
# API  → http://localhost:8000   UI → http://localhost:5173
```

## 1. Ingest + excavate (Component 1–4)

```bash
python -m scripts.seed                      # ingest the bundled sample fossils
# ingest a document we'll also time-travel in step 4 (layer v1):
curl -s localhost:8000/ingest -H 'content-type: application/json' -d '{
  "filename":"velociraptor.txt","source_id":"velociraptor.txt","layer_version":1,
  "text":"Velociraptor was a small dromaeosaurid of the Late Cretaceous. A distinctive sickle-shaped claw on each foot is its best-known feature."
}'
curl 'localhost:8000/excavate?q=sickle-shaped+claw&k=3'
```
Show the ranked **fossil cards**: cosine score, `geological_age`, `layer_version`,
`source_id`. In the UI, the **Excavate** tab does the same with the stratigraphic
card design.

## 2. Mutate + Prompt Fossilization (mutation)

```bash
curl -s localhost:8000/mutate -H 'content-type: application/json' \
  -d '{"query":"how did velociraptor hunt?","k":3}' | jq '{summary, cached, model_id}'
# run it again → "cached": true   (the prompt-fossilization cache, instant, no LLM call)
```
The **Mutate** tab shows the grounding fossils + a `cached` badge.

## 3. Chat-Based Excavation (use case #2)

UI **Chat** tab: ask "What's special about its foot?" → grounded answer with
**citations** (each cites the fossil + geological age). Cached per dialogue.

## 4. Time-Travel + Fossil Diff (mutations)

```bash
# save a revised layer (v2) of the source ingested in step 1, then compare:
curl -s localhost:8000/ingest -H 'content-type: application/json' -d '{
  "filename":"velociraptor.txt","source_id":"velociraptor.txt","layer_version":2,
  "text":"Velociraptor was a small, feathered dromaeosaurid of the Late Cretaceous. Its retractable, sickle-shaped second-toe claw is its best-known feature."
}'
curl 'localhost:8000/timetravel?source_id=velociraptor.txt'      # latest layer + available versions [1, 2]
curl 'localhost:8000/diff?source_id=velociraptor.txt&from_version=1&to_version=2'
```
UI **Fossil Layers** tab: time-travel to a layer, then diff two — the unified
diff renders with +/- coloring (the geological record of an edit).

## 5. PowerPoint Slide Mutator (use case #1)

UI **Slide Mutator** tab: paste slide text + an instruction ("make it punchier"),
get a suggestion + diff; tick **Persist** to save it as a new fossil layer
(then visible in Time-Travel / Diff). Version tracking, end to end.

## 6. Automated Enrichment (use case #3)

UI **Enrich** tab (or `POST /enrich`): paste a dig log → structured **markers**
(dates, metrics, error codes) with counts.

## 7. Fine-Tuning Dataset Builder (mutation)

UI **Dataset** tab (or `POST /dataset`): build JSONL instruction/response pairs
(chat or alpaca) from a document's gold layer.

## 8. The emulated serverless ingestion loop (Component 6 + mutations)

```bash
export LOCALSTACK_AUTH_TOKEN=...            # free for students/OSS
make up-aws                                  # + LocalStack + bootstrap + worker
make demo                                    # upload→S3→SQS→worker→silver, then
                                             # re-upload to prove self-healing idempotency
```
`make demo` shows the decoupled loop and that a re-drop is a no-op (silver
`LastModified` frozen; the DynamoDB ledger reports it already processed).

## 9. Live-ready cloud (Component 5)

```bash
cd infra && terraform init && terraform validate    # $0, what CI runs
terraform plan                                       # needs AWS creds
# terraform apply  → goes live (S3/SQS/DynamoDB/Lambda/API-GW/AOSS + alarms/dashboard)
```

## 10. Load + observability

```bash
make loadtest                                # p50/p90/p95/p99 + throughput on /excavate
```
EMF metrics (namespace `FossilRAG`), correlation ids (`X-Request-ID`), and the
CloudWatch alarms/dashboard are described in `docs/observability.md`.

```bash
make down
```
