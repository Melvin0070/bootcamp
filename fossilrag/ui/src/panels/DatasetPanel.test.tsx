import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { DatasetResponse } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { DatasetPanel } from "./DatasetPanel";

const RES: DatasetResponse = {
  source_id: "report.txt",
  version: 1,
  format: "chat",
  count: 2,
  records: [],
  jsonl: '{"messages":[{"role":"user","content":"q"}]}\n{"messages":[]}',
};

describe("DatasetPanel", () => {
  it("builds a dataset and shows the count and jsonl", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<DatasetPanel />);
    await user.type(screen.getByLabelText("Source id"), "report.txt");
    await user.click(screen.getByRole("button", { name: /build/i }));

    expect(await screen.findByText(/2 pairs/)).toBeInTheDocument();
    expect(screen.getByLabelText("dataset jsonl")).toHaveTextContent(/role.*user/);
  });

  it("surfaces a 404 for an unknown source", async () => {
    mockFetchJson({ detail: "no fossil layers" }, false, 404);
    const user = userEvent.setup();
    render(<DatasetPanel />);
    await user.type(screen.getByLabelText("Source id"), "ghost");
    await user.click(screen.getByRole("button", { name: /build/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no fossil layers/i);
  });
});
