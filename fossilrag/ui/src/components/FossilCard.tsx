import type { ExcavateHit } from "../api/types";

/** A single excavated fossil: its text plus provenance (score, layer, age, source). */
export function FossilCard({ hit }: { hit: ExcavateHit }) {
  return (
    <li className="fossil-card">
      <div className="fossil-card__bar">
        <span className="fossil-card__score" title="cosine similarity">
          {hit.score.toFixed(3)}
        </span>
        <span className="fossil-card__age">{hit.geological_age}</span>
        <span className="fossil-card__layer">layer v{hit.layer_version}</span>
      </div>
      <p className="fossil-card__content">{hit.content}</p>
      {hit.source_id ? <p className="fossil-card__source">{hit.source_id}</p> : null}
    </li>
  );
}
