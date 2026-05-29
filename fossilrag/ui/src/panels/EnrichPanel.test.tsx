import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { EnrichmentRecord } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { EnrichPanel } from "./EnrichPanel";

const RES: EnrichmentRecord = {
  source_id: "dig-log.txt",
  doc_id: "d1",
  layer_version: 1,
  markers: [
    { kind: "date", text: "2026-05-29", value: null },
    { kind: "metric", text: "92%", value: "92" },
    { kind: "error_code", text: "E-117", value: null },
  ],
  counts: { date: 1, metric: 1, error_code: 1 },
};

describe("EnrichPanel", () => {
  it("extracts and groups markers with counts", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<EnrichPanel />);
    await user.click(screen.getByRole("button", { name: /extract markers/i }));

    expect(await screen.findByText("2026-05-29")).toBeInTheDocument();
    expect(screen.getByText("E-117")).toBeInTheDocument();
    expect(screen.getByText("date: 1")).toBeInTheDocument();
  });

  it("surfaces an error", async () => {
    mockFetchJson({ detail: "bad document" }, false, 422);
    const user = userEvent.setup();
    render(<EnrichPanel />);
    await user.click(screen.getByRole("button", { name: /extract markers/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/bad document/i);
  });
});
