import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { FossilCard } from "../components/FossilCard";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /excavate — top-k semantic search over the fossil layers. */
export function ExcavatePanel() {
  const [q, setQ] = useState("");
  const [k, setK] = useState(5);
  const [sourceId, setSourceId] = useState("");
  const search = useAsyncAction(api.excavate);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const query = q.trim();
    if (query) search.run(query, k, sourceId.trim() || undefined);
  };

  const hits = search.data?.hits ?? [];

  return (
    <section className="panel" aria-labelledby="excavate-heading">
      <h2 id="excavate-heading">Excavate</h2>
      <p className="panel__lede">Top-k semantic search across every fossil layer.</p>

      <form className="form" onSubmit={onSubmit}>
        <label className="field field--grow">
          <span>Query</span>
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. sickle-shaped claw"
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
        <label className="field">
          <span>Source (optional)</span>
          <input
            type="text"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            placeholder="scope to one document"
          />
        </label>
        <button type="submit" disabled={search.status === "loading" || !q.trim()}>
          {search.status === "loading" ? "Excavating…" : "Excavate"}
        </button>
      </form>

      <AsyncBlock
        status={search.status}
        error={search.error}
        isEmpty={hits.length === 0}
        idleLabel="Enter a query to excavate fossils."
      >
        <ul className="hits">
          {hits.map((hit) => (
            <FossilCard key={`${hit.chunk_id}:${hit.layer_version}`} hit={hit} />
          ))}
        </ul>
        {search.data ? (
          <p className="meta">
            {hits.length} hits · {search.data.latency_ms.toFixed(1)} ms
          </p>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
