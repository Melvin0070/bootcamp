import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import { AsyncBlock } from "../components/AsyncBlock";
import { Badge } from "../components/Badge";
import { DiffView } from "../components/DiffView";
import { useAsyncAction } from "../hooks/useAsyncAction";

/** /slide/mutate — propose an edit, show the diff, optionally save as a new layer. */
export function SlideMutatePanel() {
  const [text, setText] = useState("Our product is good and has many features.");
  const [instruction, setInstruction] = useState("make it punchier and more specific");
  const [sourceId, setSourceId] = useState("");
  const [persist, setPersist] = useState(false);
  const slide = useAsyncAction(api.slideMutate);
  const res = slide.data;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (text.trim() && instruction.trim()) {
      slide.run({
        text,
        instruction: instruction.trim(),
        source_id: sourceId.trim() || null,
        persist,
      });
    }
  };

  return (
    <section className="panel" aria-labelledby="slide-heading">
      <h2 id="slide-heading">Slide Mutator</h2>
      <p className="panel__lede">
        Suggest a revision to slide text, see the diff, and optionally fossilise it as a new layer.
      </p>

      <form className="form form--stack" onSubmit={onSubmit}>
        <label className="field field--grow">
          <span>Slide text</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            aria-label="Slide text"
          />
        </label>
        <div className="form">
          <label className="field field--grow">
            <span>Instruction</span>
            <input
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              aria-label="Instruction"
            />
          </label>
          <label className="field">
            <span>Source id (optional)</span>
            <input
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              aria-label="Source id"
            />
          </label>
          <label className="field field--check">
            <span>Persist</span>
            <input
              type="checkbox"
              checked={persist}
              onChange={(e) => setPersist(e.target.checked)}
              aria-label="Persist as new layer"
            />
          </label>
        </div>
        <button type="submit" disabled={slide.status === "loading" || !text.trim()}>
          {slide.status === "loading" ? "Revising…" : "Suggest edit"}
        </button>
      </form>

      <AsyncBlock
        status={slide.status}
        error={slide.error}
        idleLabel="Enter slide text and an instruction."
      >
        {res ? (
          <div className="result">
            <div className="result__bar">
              <Badge tone={res.mock ? "mock" : "live"}>{res.model_id}</Badge>
              {res.cached ? <Badge tone="cached">cached</Badge> : null}
              <Badge tone={res.changed ? "changed" : "neutral"}>
                {res.changed ? "changed" : "no change"}
              </Badge>
              {res.persisted_version ? (
                <Badge tone="live">saved as v{res.persisted_version}</Badge>
              ) : null}
            </div>
            <p className="result__text">{res.suggestion}</p>
            <DiffView diff={res.unified_diff} />
          </div>
        ) : null}
      </AsyncBlock>
    </section>
  );
}
