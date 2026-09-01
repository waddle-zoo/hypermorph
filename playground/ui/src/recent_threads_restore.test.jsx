import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { HypersetChat, readThreadRestore, THREAD_RESTORE_KEY } from "@hyperset/chat-ui";
import { LiveChatPage, RecentThreadsPage } from "./main.jsx";

// hy-87n1 (Explorer gap 9): reopening a Recent thread RESTORES the thread's state -- the
// question AND the run settings it was run with (agent / model / run mode) -- not just a
// re-prefill of the question. The page hands those settings to the next chat mount via the
// browser-local restore key.

beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  };
});

const THREAD = {
  id: "t1",
  question: "recognized revenue by region",
  answer: "It is the completed-order revenue net of tax.",
  createdAt: 1_700_000_000_000,
  agent: "revenue-agent",
  model: "gpt-5.6-luna",
  governedOnly: false,
};

describe("RecentThreadsPage restore (hy-87n1 gap 9)", () => {
  it("surfaces the saved run settings and hands the full thread state to the next mount", () => {
    localStorage.setItem("hyperset-threads", JSON.stringify([THREAD]));
    render(<RecentThreadsPage />);

    // The card shows the run mode it was run with, not just the question.
    expect(screen.getByText("recognized revenue by region")).toBeInTheDocument();
    expect(screen.getByText(/Governed \+ observed/)).toBeInTheDocument();

    // Opening the thread writes the restore handoff carrying question + agent + model + mode.
    fireEvent.click(screen.getByText("Open thread"));
    const handoff = JSON.parse(localStorage.getItem(THREAD_RESTORE_KEY));
    expect(handoff).toMatchObject({
      question: "recognized revenue by region",
      answer: "It is the completed-order revenue net of tax.",
      agent: "revenue-agent",
      model: "gpt-5.6-luna",
      governedOnly: false,
    });
    expect(handoff.messages.map(({ role, content }) => ({ role, content }))).toEqual([
      { role: "user", content: THREAD.question },
      { role: "assistant", content: THREAD.answer },
    ]);
    // The consumer reads it back intact (proving the restore path, not just the question).
    expect(readThreadRestore(localStorage)).toMatchObject({ agent: "revenue-agent", governedOnly: false });
  });

  it("redacts a credential in a legacy cleartext thread on read and on the reopen handoff", () => {
    // A record written by older code could hold a credential URL in cleartext; the read
    // path (RecentThreadsPage) and the reopen handoff both redact it (hy-87n1 critic).
    localStorage.setItem(
      "hyperset-threads",
      JSON.stringify([
        { id: "leak", question: "open https://user:supersecret@warehouse.example/db", answer: "a", createdAt: 1, governedOnly: false },
      ]),
    );
    const { container } = render(<RecentThreadsPage />);
    expect(container.textContent).not.toContain("supersecret");
    expect(container.textContent).toContain("warehouse.example");

    fireEvent.click(screen.getByText("Open thread"));
    const handoff = localStorage.getItem(THREAD_RESTORE_KEY);
    expect(handoff).not.toContain("supersecret");
    expect(JSON.parse(handoff).question).toContain("warehouse.example");
  });

  it("a legacy thread without settings restores as governed-only", () => {
    localStorage.setItem(
      "hyperset-threads",
      JSON.stringify([{ id: "old", question: "q", answer: "a", createdAt: 1 }]),
    );
    render(<RecentThreadsPage />);
    expect(screen.getByText(/Governed only/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Open thread"));
    expect(JSON.parse(localStorage.getItem(THREAD_RESTORE_KEY)).governedOnly).toBe(true);
  });

  it("reopens the saved question and answer as messages with an empty composer", () => {
    localStorage.setItem("hyperset-threads", JSON.stringify([THREAD]));
    render(<RecentThreadsPage />);
    fireEvent.click(screen.getByText("Open thread"));
    const restored = readThreadRestore(localStorage);
    cleanup();

    render(<HypersetChat
      agent={restored.agent}
      model={restored.model}
      agents={[{ value: "revenue-agent", label: "Revenue agent" }]}
      models={[{ value: "gpt-5.6-luna", label: "gpt-5.6-luna · openai", provider: "openai" }]}
      backendHealthy
      setAgent={() => {}}
      setModel={() => {}}
      initialMessages={restored.messages}
      initialGovernedOnly={restored.governedOnly}
    />);

    expect(screen.getByText(THREAD.question)).toBeInTheDocument();
    expect(screen.getByText(THREAD.answer)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Ask about revenue/i)).toHaveValue("");
    expect(screen.getByRole("radio", { name: "Governed + observed" })).toHaveAttribute("aria-checked", "true");
  });

  it("places Recent chats before the conversation and moves it after chat at the narrow breakpoint", () => {
    localStorage.setItem("hyperset-threads", JSON.stringify([THREAD]));
    const { container } = render(<LiveChatPage />);
    const layout = screen.getByTestId("live-chat-layout");
    expect(layout.children[0]).toHaveAttribute("aria-label", "Recent chats");
    expect(layout.children[1]).toHaveClass("live-chat-column");
    const scopedCss = [...container.querySelectorAll("style")].map((style) => style.textContent).join("\n");
    expect(scopedCss).toContain("grid-template-columns: 248px minmax(0, 1fr)");
    expect(scopedCss).toContain("@media (max-width: 900px)");
    expect(scopedCss).toContain(".recent-chats-panel { order: 2; }");
  });
});
