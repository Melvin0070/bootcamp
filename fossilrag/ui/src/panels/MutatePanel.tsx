import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { Badge } from "../components/Badge";
import { FossilCard } from "../components/FossilCard";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /mutate — retrieve relevant fossils and return a grounded LLM summary/edit. */
export function MutatePanel() {
  const [query, setQuery] = useState("");
  const [instruction, setInstruction] = useState("");
  const [k, setK] = useState(5);
  const mutate = useAsyncAction(api.mutate);
  const res = mutate.data;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (q) mutate.run({ query: q, k, instruction: instruction.trim() || null });
  };

  return (
    <section className="panel" aria-labelledby="mutate-heading">
      <h2 id="mutate-heading">Mutate</h2>
      <p className="panel__lede">
        A grounded LLM summary over the top-k fossils, with prompt fossilization (output cache).
      </p>

      <form className="form" onSubmit={onSubmit}>
        <label className="field field--grow">
          <span>Query</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="what do we know about…"
            aria-label="Query"
          />
        </label>
        <label className="field field--narrow">
          <span>Top-k</span>
          <input
            type="number"
            min={1}
            max={50}
            value={k}
            onChange={(e) => setK(Number(e.target.value))}
            aria-label="Top-k"
          />
        </label>
        <label className="field field--grow">
          <span>Instruction (optional)</span>
          <input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="e.g. summarise for a 10-year-old"
          />
        </label>
        <button type="submit" disabled={mutate.status === "loading" || !query.trim()}>
          {mutate.status === "loading" ? "Mutating…" : "Mutate"}
        </button>
      </form>

      <AsyncBlock
        status={mutate.status}
        error={mutate.error}
        idleLabel="Ask a question to synthesise an answer from the fossils."
      >
        {res ? (
          <div className="result">
            <div className="result__bar">
              <Badge tone={res.mock ? "mock" : "live"}>{res.model_id}</Badge>
              {res.cached ? <Badge tone="cached">cached</Badge> : null}
              <span className="meta">{res.latency_ms.toFixed(1)} ms</span>
            </div>
            <p className="result__text">{res.summary}</p>
            {res.note ? <p className="result__note">{res.note}</p> : null}
            {res.used_chunks.length > 0 ? (
              <details className="result__sources">
                <summary>{res.used_chunks.length} grounding fossils</summary>
                <ul className="hits">
                  {res.used_chunks.map((hit) => (
                    <FossilCard key={`${hit.chunk_id}:${hit.layer_version}`} hit={hit} />
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
