import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SlideMutateResponse } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { SlideMutatePanel } from "./SlideMutatePanel";

const RES: SlideMutateResponse = {
  source_id: null,
  instruction: "punchier",
  original: "Our product is good.",
  suggestion: "Ships 3x faster, zero config.",
  model_id: "mock-llm-v1",
  mock: true,
  cached: false,
  changed: true,
  added_lines: 1,
  removed_lines: 1,
  unified_diff:
    "--- original\n+++ suggestion\n@@ -1 +1 @@\n-Our product is good.\n+Ships 3x faster, zero config.",
};

describe("SlideMutatePanel", () => {
  it("renders the suggestion, a changed badge and the diff", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<SlideMutatePanel />);
    await user.click(screen.getByRole("button", { name: /suggest edit/i }));

    expect(await screen.findByText("changed")).toBeInTheDocument();
    expect(screen.getAllByText(/Ships 3x faster/i).length).toBeGreaterThan(0);
  });

  it("surfaces an LLM error", async () => {
    mockFetchJson({ detail: "LLM provider error" }, false, 502);
    const user = userEvent.setup();
    render(<SlideMutatePanel />);
    await user.click(screen.getByRole("button", { name: /suggest edit/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/LLM provider error/i);
  });
});
