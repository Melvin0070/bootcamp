import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MutateResponse } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { MutatePanel } from "./MutatePanel";

const RES: MutateResponse = {
  query: "claws",
  instruction: null,
  summary: "Velociraptors are known for a sickle-shaped claw.",
  model_id: "mock-llm-v1",
  mock: true,
  cached: true,
  used_chunks: [],
  note: "(mock summary)",
  latency_ms: 5.1,
};

describe("MutatePanel", () => {
  it("renders the grounded summary with model and cached badge", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<MutatePanel />);
    await user.type(screen.getByLabelText("Query"), "claws");
    await user.click(screen.getByRole("button", { name: /mutate/i }));

    expect(await screen.findByText(/sickle-shaped claw/i)).toBeInTheDocument();
    expect(screen.getByText("mock-llm-v1")).toBeInTheDocument();
    expect(screen.getByText("cached")).toBeInTheDocument();
  });

  it("surfaces an LLM error as an alert", async () => {
    mockFetchJson({ detail: "LLM provider error" }, false, 502);
    const user = userEvent.setup();
    render(<MutatePanel />);
    await user.type(screen.getByLabelText("Query"), "x");
    await user.click(screen.getByRole("button", { name: /mutate/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/LLM provider error/i);
  });
});
