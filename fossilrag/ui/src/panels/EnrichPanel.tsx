import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { useAsyncAction } from "../hooks/useAsyncAction";

const SAMPLE =
  "On 2026-05-29 the rig hit 92% utilisation. Error code E-117 recurred 3 times " +
  "before the failover at 14:05 UTC restored throughput to 1.2 GB/s.";

/** /enrich — extract structured markers (dates, metrics, error codes) from a document. */
export function EnrichPanel() {
  const [filename, setFilename] = useState("dig-log.txt");
  const [text, setText] = useState(SAMPLE);
  const [sourceId, setSourceId] = useState("");
  const enrich = useAsyncAction(api.enrich);
  const res = enrich.data;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (filename.trim() && text.trim()) {
      enrich.run({
        filename: filename.trim(),
        text,
        content_type: "text/plain",
        source_id: sourceId.trim() || null,
        layer_version: 1,
      });
    }
  };

  return (
    <section className="panel" aria-labelledby="enrich-heading">
      <h2 id="enrich-heading">Enrich</h2>
      <p className="panel__lede">
        Mine a report for structured markers — dates, metrics, error codes — into a record.
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
            <input value={sourceId} onChange={(e) => setSourceId(e.target.value)} />
          </label>
        </div>
        <label className="field field--grow">
          <span>Text</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            aria-label="Text"
          />
        </label>
        <button type="submit" disabled={enrich.status === "loading" || !text.trim()}>
          {enrich.status === "loading" ? "Mining…" : "Extract markers"}
        </button>
      </form>

      <AsyncBlock
        status={enrich.status}
        error={enrich.error}
        isEmpty={res?.markers.length === 0}
        emptyLabel="No markers found in this text."
        idleLabel="Paste a report to extract its markers."
      >
        {res ? (
          <div className="result">
            <div className="result__bar">
              {Object.entries(res.counts).map(([kind, n]) => (
                <span key={kind} className="badge badge--neutral">
                  {kind}: {n}
                </span>
              ))}
            </div>
            <ul className="markers">
              {res.markers.map((m, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: markers are an ordered extraction list
                <li key={i} className="marker">
                  <span className="marker__kind">{m.kind}</span>
                  <span className="marker__text">{m.text}</span>
                  {m.value ? <span className="marker__value">{m.value}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
