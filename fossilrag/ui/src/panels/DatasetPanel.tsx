import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /dataset — build a fine-tuning JSONL (chat | alpaca) from a document's gold layer. */
export function DatasetPanel() {
  const [sourceId, setSourceId] = useState("");
  const [version, setVersion] = useState("");
  const [fmt, setFmt] = useState("chat");
  const dataset = useAsyncAction(api.dataset);
  const res = dataset.data;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const sid = sourceId.trim();
    if (sid) dataset.run({ source_id: sid, version: version.trim() ? Number(version) : null, fmt });
  };

  return (
    <section className="panel" aria-labelledby="dataset-heading">
      <h2 id="dataset-heading">Dataset Builder</h2>
      <p className="panel__lede">
        Generate instruction/response pairs (JSONL) from a document's fossils for fine-tuning.
      </p>

      <form className="form" onSubmit={onSubmit}>
        <label className="field field--grow">
          <span>Source id</span>
          <input
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            aria-label="Source id"
          />
        </label>
        <label className="field field--narrow">
          <span>Layer (optional)</span>
          <input
            type="number"
            min={1}
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="latest"
          />
        </label>
        <label className="field">
          <span>Format</span>
          <select value={fmt} onChange={(e) => setFmt(e.target.value)} aria-label="Format">
            <option value="chat">chat</option>
            <option value="alpaca">alpaca</option>
          </select>
        </label>
        <button type="submit" disabled={dataset.status === "loading" || !sourceId.trim()}>
          Build
        </button>
      </form>

      <AsyncBlock
        status={dataset.status}
        error={dataset.error}
        idleLabel="Pick a document to build a dataset."
      >
        {res ? (
          <div className="result">
            <p className="meta">
              {res.count} pairs · format {res.format} · layer v{res.version}
            </p>
            <pre className="jsonl" aria-label="dataset jsonl">
              {res.jsonl}
            </pre>
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
