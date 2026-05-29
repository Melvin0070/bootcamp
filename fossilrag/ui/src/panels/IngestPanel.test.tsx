import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngestResult } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { IngestPanel } from "./IngestPanel";

const RES: IngestResult = {
  doc_id: "abcdef0123456789aa",
  filename: "velociraptor.txt",
  chunks_total: 3,
  chunks_indexed: 3,
  layer_version: 1,
  embed_model: "mock-deterministic-v1",
  embed_dim: 384,
};

describe("IngestPanel", () => {
  it("ingests the prefilled sample and reports the indexed chunks", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<IngestPanel />);
    await user.click(screen.getByRole("button", { name: /ingest/i }));

    expect(await screen.findByText(/Indexed/)).toBeInTheDocument();
    expect(screen.getByText("mock-deterministic-v1")).toBeInTheDocument();
  });

  it("surfaces a validation error", async () => {
    mockFetchJson({ detail: "empty document" }, false, 422);
    const user = userEvent.setup();
    render(<IngestPanel />);
    await user.click(screen.getByRole("button", { name: /ingest/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/empty document/i);
  });
});
