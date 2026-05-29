import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { FossilDiffResponse, TimeTravelResponse } from "../api/types";
import { mockFetchSequence } from "../test-utils";
import { FossilLayersPanel } from "./FossilLayersPanel";

const TRAVEL: TimeTravelResponse = {
  source_id: "report.txt",
  version: 2,
  latest_version: 2,
  available_versions: [1, 2],
  is_latest: true,
  chunks: [
    {
      chunk_id: "c1",
      doc_id: "d1",
      source_id: "report.txt",
      ordinal: 0,
      content: "The dig reached the second stratum.",
      layer_version: 2,
      geological_age: "Holocene",
    },
  ],
};

const DIFF: FossilDiffResponse = {
  source_id: "report.txt",
  from_version: 1,
  to_version: 2,
  changed: true,
  added_lines: 1,
  removed_lines: 0,
  unified_diff: "--- v1\n+++ v2\n@@ -0,0 +1 @@\n+a newly excavated line",
};

describe("FossilLayersPanel", () => {
  it("time-travels to a layer, then diffs two layers", async () => {
    mockFetchSequence({ body: TRAVEL }, { body: DIFF });
    const user = userEvent.setup();
    render(<FossilLayersPanel />);

    await user.type(screen.getByLabelText("Source id"), "report.txt");
    await user.click(screen.getByRole("button", { name: /time-travel/i }));

    expect(await screen.findByText(/second stratum/i)).toBeInTheDocument();
    expect(screen.getByText(/available: 1, 2/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^diff$/i }));
    expect(await screen.findByText(/newly excavated line/i)).toBeInTheDocument();
    expect(screen.getByText("changed")).toBeInTheDocument();
  });

  it("hides a stale diff after switching to a different source", async () => {
    const TRAVEL_B: TimeTravelResponse = {
      ...TRAVEL,
      source_id: "other.txt",
      chunks: [
        { ...TRAVEL.chunks[0], source_id: "other.txt", content: "A different stratum entirely." },
      ],
    };
    mockFetchSequence({ body: TRAVEL }, { body: DIFF }, { body: TRAVEL_B });
    const user = userEvent.setup();
    render(<FossilLayersPanel />);

    await user.type(screen.getByLabelText("Source id"), "report.txt");
    await user.click(screen.getByRole("button", { name: /time-travel/i }));
    await screen.findByText(/second stratum/i);
    await user.click(screen.getByRole("button", { name: /^diff$/i }));
    expect(await screen.findByText(/newly excavated line/i)).toBeInTheDocument();

    // Switch to a different source and time-travel: the diff for the OLD source
    // must not linger under the new layer.
    const source = screen.getByLabelText("Source id");
    await user.clear(source);
    await user.type(source, "other.txt");
    await user.click(screen.getByRole("button", { name: /time-travel/i }));
    expect(await screen.findByText(/different stratum entirely/i)).toBeInTheDocument();
    expect(screen.queryByText(/newly excavated line/i)).not.toBeInTheDocument();
  });

  it("surfaces a 404 for an unknown source", async () => {
    mockFetchSequence({ body: { detail: "no fossil layers" }, ok: false, status: 404 });
    const user = userEvent.setup();
    render(<FossilLayersPanel />);
    await user.type(screen.getByLabelText("Source id"), "ghost");
    await user.click(screen.getByRole("button", { name: /time-travel/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no fossil layers/i);
  });
});
