import type { ReactNode } from "react";

type Tone = "neutral" | "cached" | "mock" | "live" | "changed";

/** A small status pill (cached / mock / live / changed …). */
export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}
