import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { Badge } from "../components/Badge";
import { DiffView } from "../components/DiffView";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /timetravel + /diff — inspect a document's fossil layers and diff two of them. */
export function FossilLayersPanel() {
  const [sourceId, setSourceId] = useState("");
  const [version, setVersion] = useState<string>("");
  const [fromV, setFromV] = useState(1);
  const [toV, setToV] = useState(2);

  const travel = useAsyncAction(api.timetravel);
  const diff = useAsyncAction(api.diff);
  const layer = travel.data;
  const d = diff.data;

  const onTravel = (e: FormEvent) => {
    e.preventDefault();
    const sid = sourceId.trim();
    if (sid) travel.run(sid, version.trim() ? Number(version) : undefined);
  };

  const onDiff = () => {
    const sid = sourceId.trim();
    if (sid) diff.run(sid, fromV, toV);
  };

  return (
    <section className="panel" aria-labelledby="layers-heading">
      <h2 id="layers-heading">Fossil Layers</h2>
      <p className="panel__lede">
        Time-travel to any layer of a document, then diff two layers (the geological record).
      </p>

      <form className="form" onSubmit={onTravel}>
        <label className="field field--grow">
          <span>Source id</span>
          <input
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            placeholder="the document's stable id"
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
            aria-label="Layer version"
          />
        </label>
        <button type="submit" disabled={travel.status === "loading" || !sourceId.trim()}>
          Time-travel
        </button>
      </form>

      <AsyncBlock
        status={travel.status}
        error={travel.error}
        idleLabel="Enter a source id to inspect its fossil layers."
      >
        {layer ? (
          <div className="result">
            <div className="result__bar">
              <Badge tone={layer.is_latest ? "live" : "neutral"}>
                layer v{layer.version}
                {layer.is_latest ? " (latest)" : ""}
              </Badge>
              <span className="meta">available: {layer.available_versions.join(", ")}</span>
            </div>
            <ul className="hits">
              {layer.chunks.map((c) => (
                <li key={c.chunk_id} className="fossil-card">
                  <div className="fossil-card__bar">
                    <span className="fossil-card__age">{c.geological_age}</span>
                    <span className="fossil-card__layer">#{c.ordinal}</span>
                  </div>
                  <p className="fossil-card__content">{c.content}</p>
                </li>
              ))}
            </ul>

            <fieldset className="diff-controls">
              <legend>Diff two layers</legend>
              <label className="field field--narrow">
                <span>From</span>
                <input
                  type="number"
                  min={1}
                  value={fromV}
                  onChange={(e) => setFromV(Number(e.target.value))}
                  aria-label="From version"
                />
              </label>
              <label className="field field--narrow">
                <span>To</span>
                <input
                  type="number"
                  min={1}
                  value={toV}
                  onChange={(e) => setToV(Number(e.target.value))}
                  aria-label="To version"
                />
              </label>
              <button type="button" onClick={onDiff} disabled={diff.status === "loading"}>
                Diff
              </button>
            </fieldset>

            <AsyncBlock status={diff.status} error={diff.error}>
              {d ? (
                <div className="result">
                  <div className="result__bar">
                    <Badge tone={d.changed ? "changed" : "neutral"}>
                      {d.changed ? "changed" : "identical"}
                    </Badge>
                    <span className="meta">
                      +{d.added_lines} −{d.removed_lines}
                    </span>
                  </div>
                  <DiffView diff={d.unified_diff} />
                </div>
              ) : null}
            </AsyncBlock>
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
