/** Render a unified diff with +/- line coloring. */
export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <p className="status status--empty">No changes between these layers.</p>;
  }
  const lines = diff.split("\n");
  return (
    <pre className="diff" aria-label="unified diff">
      {lines.map((line, i) => {
        let cls = "diff__ctx";
        if (line.startsWith("+") && !line.startsWith("+++")) cls = "diff__add";
        else if (line.startsWith("-") && !line.startsWith("---")) cls = "diff__del";
        else if (line.startsWith("@@")) cls = "diff__hunk";
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: diff lines are a stable ordered list
          <span key={i} className={cls}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}
