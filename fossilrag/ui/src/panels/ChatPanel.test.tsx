import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ChatResponse } from "../api/types";
import { mockFetchJson } from "../test-utils";
import { ChatPanel } from "./ChatPanel";

const RES: ChatResponse = {
  answer: "It hunted with a sickle-shaped claw.",
  model_id: "mock-llm-v1",
  mock: true,
  cached: false,
  citations: [],
  latency_ms: 4.2,
};

describe("ChatPanel", () => {
  it("appends the user turn and the assistant reply", async () => {
    mockFetchJson(RES);
    const user = userEvent.setup();
    render(<ChatPanel />);
    await user.type(screen.getByLabelText("Message"), "how did it hunt?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText("how did it hunt?")).toBeInTheDocument();
    expect(await screen.findByText(/sickle-shaped claw/i)).toBeInTheDocument();
    expect(screen.getByText("mock-llm-v1")).toBeInTheDocument();
  });

  it("shows an alert and restores the draft when the turn fails", async () => {
    mockFetchJson({ detail: "retrieval failed" }, false, 500);
    const user = userEvent.setup();
    render(<ChatPanel />);
    await user.type(screen.getByLabelText("Message"), "boom");
    await user.click(screen.getByRole("button", { name: /send/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/retrieval failed/i);
    // The failed turn's text is restored so it can be re-sent (not lost).
    expect(screen.getByLabelText("Message")).toHaveValue("boom");
  });
});
