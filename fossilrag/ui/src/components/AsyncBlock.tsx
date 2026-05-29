import type { ReactNode } from "react";
import type { AsyncStatus } from "../hooks/useAsyncAction";

interface Props {
  status: AsyncStatus;
  error?: string;
  isEmpty?: boolean;
  idleLabel?: string;
  loadingLabel?: string;
  emptyLabel?: string;
  children?: ReactNode;
}

/** Renders the right thing for an async action's idle/loading/error/empty/success state. */
export function AsyncBlock({
  status,
  error,
  isEmpty,
  idleLabel,
  loadingLabel = "Excavating…",
  emptyLabel = "No fossils matched.",
  children,
}: Props) {
  if (status === "loading") {
    // <output> carries an implicit ARIA "status" live region (announced politely).
    return <output className="status status--loading">{loadingLabel}</output>;
  }
  if (status === "error") {
    return (
      <p className="status status--error" role="alert">
        {error ?? "Something went wrong."}
      </p>
    );
  }
  if (status === "idle") {
    return idleLabel ? <p className="status status--idle">{idleLabel}</p> : null;
  }
  if (isEmpty) {
    return <p className="status status--empty">{emptyLabel}</p>;
  }
  return <>{children}</>;
}
