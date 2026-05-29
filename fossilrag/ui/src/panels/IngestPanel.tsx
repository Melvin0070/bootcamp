import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { useAsyncAction } from "../hooks/useAsyncAction";

const SAMPLE =
  "Velociraptor was a small dromaeosaurid dinosaur.\n\n" +
  "It lived during the Late Cretaceous period in what is now Mongolia.\n\n" +
  "A distinctive sickle-shaped claw on each foot is its best-known feature.";

/** /ingest — push a document through the full spine (extract → chunk → embed → index). */
export function IngestPanel() {
  const [filename, setFilename] = useState("velociraptor.txt");
  const [text, setText] = useState(SAMPLE);
  const [sourceId, setSourceId] = useState("");
  const [layer, setLayer] = useState(1);
  const ingest = useAsyncAction(api.ingest);
  const res = ingest.data;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (filename.trim() && text.trim()) {
      ingest.run({
        filename: filename.trim(),
        text,
        content_type: "text/plain",
        source_id: sourceId.trim() || null,
        layer_version: layer,
      });
    }
  };

  return (
    <section className="panel" aria-labelledby="ingest-heading">
      <h2 id="ingest-heading">Ingest</h2>
      <p className="panel__lede">
        Seed a fossil: extract, clean, chunk, embed and index a document into a layer.
      </p>

      <form className="form form--stack" onSubmit={onSubmit}>
        <div className="form">
          <label className="field field--grow">
            <span>Filename</span>
            <input
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              aria-label="Filename"
            />
          </label>
          <label className="field">
            <span>Source id (optional)</span>
            <input
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              placeholder="defaults to filename"
              aria-label="Source id"
            />
          </label>
          <label className="field field--narrow">
            <span>Layer</span>
            <input
              type="number"
              min={1}
              value={layer}
              onChange={(e) => setLayer(Number(e.target.value))}
              aria-label="Layer version"
            />
          </label>
        </div>
        <label className="field field--grow">
          <span>Text</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            aria-label="Text"
          />
        </label>
        <button type="submit" disabled={ingest.status === "loading" || !text.trim()}>
          {ingest.status === "loading" ? "Indexing…" : "Ingest"}
        </button>
      </form>

      <AsyncBlock
        status={ingest.status}
        error={ingest.error}
        idleLabel="Paste a document to fossilise it."
      >
        {res ? (
          <div className="result">
            <p className="result__text">
              Indexed <strong>{res.chunks_indexed}</strong> of {res.chunks_total} chunks into layer
              v{res.layer_version} via <code>{res.embed_model}</code> ({res.embed_dim}d).
            </p>
            <p className="meta">doc_id {res.doc_id.slice(0, 16)}…</p>
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
