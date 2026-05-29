import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExcavateResponse } from "../api/types";
import { ExcavatePanel } from "./ExcavatePanel";

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const RESPONSE: ExcavateResponse = {
  query: "sickle claw",
  k: 5,
  latency_ms: 12.34,
  hits: [
    {
      chunk_id: "c1",
      doc_id: "d1",
      source_id: "velociraptor.txt",
      ordinal: 0,
      content: "A distinctive sickle-shaped claw on each foot.",
      score: 0.873,
      layer_version: 1,
      geological_age: "Late Cretaceous",
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("ExcavatePanel", () => {
  it("shows an idle prompt before any search", () => {
    render(<ExcavatePanel />);
    expect(screen.getByText(/enter a query to excavate/i)).toBeInTheDocument();
  });

  it("renders excavated fossils with score, age and source", async () => {
    const fetchMock = mockFetchOnce(RESPONSE);
    const user = userEvent.setup();
    render(<ExcavatePanel />);

    await user.type(screen.getByLabelText("Query"), "sickle claw");
    await user.click(screen.getByRole("button", { name: /excavate/i }));

    expect(await screen.findByText(/sickle-shaped claw on each foot/i)).toBeInTheDocument();
    expect(screen.getByText("0.873")).toBeInTheDocument();
    expect(screen.getByText("Late Cretaceous")).toBeInTheDocument();
    expect(screen.getByText("velociraptor.txt")).toBeInTheDocument();
    expect(screen.getByText(/1 hits · 12.3 ms/)).toBeInTheDocument();

    // Hit the real endpoint path with the query + k.
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/excavate");
    expect(url).toContain("q=sickle+claw");
    expect(url).toContain("k=5");
  });

  it("shows an empty state when nothing matches", async () => {
    mockFetchOnce({ ...RESPONSE, hits: [] });
    const user = userEvent.setup();
    render(<ExcavatePanel />);
    await user.type(screen.getByLabelText("Query"), "nothing");
    await user.click(screen.getByRole("button", { name: /excavate/i }));
    expect(await screen.findByText(/no fossils matched/i)).toBeInTheDocument();
  });

  it("surfaces an API error as an alert", async () => {
    mockFetchOnce({ detail: "excavation failed" }, false, 500);
    const user = userEvent.setup();
    render(<ExcavatePanel />);
    await user.type(screen.getByLabelText("Query"), "boom");
    await user.click(screen.getByRole("button", { name: /excavate/i }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/excavation failed/i);
    expect(alert).toHaveTextContent(/500/);
  });
});
