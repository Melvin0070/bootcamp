import { useCallback, useState } from "react";
import { ApiError } from "../api/client";

export type AsyncStatus = "idle" | "loading" | "success" | "error";

export interface AsyncState<T> {
  status: AsyncStatus;
  data?: T;
  error?: string;
}

/**
 * Run an async action and track idle/loading/success/error for the UI.
 *
 * Returns the state plus a `run(...)` that invokes the action; errors are
 * caught and surfaced as `error` (never thrown into render), so every panel
 * gets loading + error + empty states for free.
 */
export function useAsyncAction<Args extends unknown[], T>(fn: (...args: Args) => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ status: "idle" });

  const run = useCallback(
    async (...args: Args): Promise<T | undefined> => {
      setState({ status: "loading" });
      try {
        const data = await fn(...args);
        setState({ status: "success", data });
        return data;
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.status
              ? `${err.message} (HTTP ${err.status})`
              : err.message
            : err instanceof Error
              ? err.message
              : "Unexpected error";
        setState({ status: "error", error: message });
        return undefined;
      }
    },
    [fn],
  );

  const reset = useCallback(() => setState({ status: "idle" }), []);

  return { ...state, run, reset };
}
