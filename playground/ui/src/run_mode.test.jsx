import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Composer, saveThreadTurn, readThreadRestore, writeThreadRestore, THREADS_KEY, THREAD_RESTORE_KEY } from "@hyperset/chat-ui";

// hy-87n1 (Explorer gap 4): the run mode is an EXPLICIT NAMED choice -- 'Governed only'
// (default) vs 'Governed + observed' -- not an unlabeled toggle-off. Both states are
// named and selectable, the default is governed-only, and the choice reaches onSend as
// `governedOnly` so the request carries it.

function memoryStore() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  };
}

function renderComposer(props = {}) {
  const onSend = vi.fn();
  render(
    <Composer
      onSend={onSend}
      sendDisabled={false}
      agent="revenue-agent"
      model="gpt-5.6-luna"
      agents={[{ value: "revenue-agent", label: "Revenue agent" }]}
      models={[{ value: "gpt-5.6-luna", label: "gpt-5.6-luna · openai", provider: "openai" }]}
      backendHealthy
      setAgent={() => {}}
      setModel={() => {}}
      {...props}
    />,
  );
  return onSend;
}

function ask(text) {
  fireEvent.change(screen.getByPlaceholderText(/Ask about revenue/i), { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send question"));
}

describe("named run mode (hy-87n1 gap 4)", () => {
  it("shows both named options with governed-only selected by default", () => {
    renderComposer();
    const governed = screen.getByRole("radio", { name: "Governed only" });
    const observed = screen.getByRole("radio", { name: "Governed + observed" });
    expect(governed).toBeInTheDocument();
    expect(observed).toBeInTheDocument();
    expect(governed).toHaveAttribute("aria-checked", "true"); // default
    expect(observed).toHaveAttribute("aria-checked", "false");
  });

  it("sends governedOnly:true by default and false once 'Governed + observed' is chosen", () => {
    const onSend = renderComposer();
    ask("what is recognized revenue");
    expect(onSend).toHaveBeenLastCalledWith("what is recognized revenue", expect.objectContaining({ governedOnly: true }));

    fireEvent.click(screen.getByRole("radio", { name: "Governed + observed" }));
    ask("same but observed");
    expect(onSend).toHaveBeenLastCalledWith("same but observed", expect.objectContaining({ governedOnly: false }));
  });

  it("restores the run mode a reopened thread carried via initialGovernedOnly", () => {
    const onSend = renderComposer({ initialGovernedOnly: false });
    // The 'Governed + observed' option is pre-selected -- the thread is restored, not reset.
    expect(screen.getByRole("radio", { name: "Governed + observed" })).toHaveAttribute("aria-checked", "true");
    ask("restored observed run");
    expect(onSend).toHaveBeenLastCalledWith("restored observed run", expect.objectContaining({ governedOnly: false }));
  });
});

describe("thread persistence + restore handoff (hy-87n1 gap 9)", () => {
  it("saveThreadTurn persists the run settings, newest-first, deduped and capped", () => {
    const store = memoryStore();
    saveThreadTurn(store, { id: "t1", question: "q1", answer: "a1", createdAt: 1, agent: "revenue-agent", model: "gpt-5.6-luna", governedOnly: false });
    let saved = JSON.parse(store.getItem(THREADS_KEY));
    expect(saved).toHaveLength(1);
    // The replayable settings are persisted, not just question/answer.
    expect(saved[0]).toMatchObject({ id: "t1", question: "q1", answer: "a1", agent: "revenue-agent", model: "gpt-5.6-luna", governedOnly: false });

    // Re-saving the same turn id dedupes (no duplicate), and a new turn lands newest-first.
    saveThreadTurn(store, { id: "t2", question: "q2", answer: "a2", createdAt: 2, agent: "revenue-agent", model: "gpt-5.6-luna", governedOnly: true });
    saveThreadTurn(store, { id: "t1", question: "q1b", answer: "a1b", createdAt: 3, agent: "x", model: "y", governedOnly: true });
    saved = JSON.parse(store.getItem(THREADS_KEY));
    expect(saved.map((t) => t.id)).toEqual(["t1", "t2"]); // deduped, most-recent first
    expect(saved[0]).toMatchObject({ question: "q1b", governedOnly: true });

    // A legacy record without governedOnly restores as governed-only (the safe default).
    saveThreadTurn(store, { id: "t3", question: "q3", answer: "a3", createdAt: 4 });
    expect(JSON.parse(store.getItem(THREADS_KEY))[0].governedOnly).toBe(true);

    // Cap.
    const capStore = memoryStore();
    for (let i = 0; i < 35; i += 1) saveThreadTurn(capStore, { id: `c${i}`, question: "q", answer: "a", createdAt: i });
    expect(JSON.parse(capStore.getItem(THREADS_KEY))).toHaveLength(30);
  });

  it("redacts URL userinfo at the persist boundary in saveThreadTurn (hy-87n1 critic, #472 lesson)", () => {
    const store = memoryStore();
    // A user pastes a credential-bearing URL into the chat -- free-form text.
    saveThreadTurn(store, {
      id: "t1",
      question: "why does https://user:supersecret@warehouse.example/db fail",
      answer: "connect https://svc:tok3n99@db.example/x and retry",
      createdAt: 1,
      agent: "revenue-agent",
      model: "gpt-5.6-luna",
      governedOnly: true,
    });
    const raw = store.getItem(THREADS_KEY);
    // No credential reaches localStorage in cleartext...
    expect(raw).not.toContain("supersecret");
    expect(raw).not.toContain("tok3n99");
    expect(raw).not.toContain("user:supersecret@");
    // ...and it is never read back cleartext either.
    const back = JSON.parse(raw)[0];
    expect(back.question).not.toContain("supersecret");
    expect(back.answer).not.toContain("tok3n99");
    // The non-secret text survives so the thread stays useful.
    expect(back.question).toContain("warehouse.example");
    expect(back.answer).toContain("db.example");
  });

  it("redacts URL userinfo at the persist boundary in writeThreadRestore", () => {
    const store = memoryStore();
    writeThreadRestore(store, {
      question: "open https://user:leaked77@gw.example/v1 please",
      agent: "revenue-agent",
      model: "gpt-5.6-luna",
      governedOnly: false,
    });
    const raw = store.getItem(THREAD_RESTORE_KEY);
    expect(raw).not.toContain("leaked77");
    expect(raw).not.toContain("user:leaked77@");
    expect(readThreadRestore(store).question).toContain("gw.example");
  });

  it("readThreadRestore returns the handoff once, then clears it", () => {
    const store = memoryStore();
    writeThreadRestore(store, { question: "q", agent: "revenue-agent", model: "gpt-5.6-luna", governedOnly: false });
    expect(store.getItem(THREAD_RESTORE_KEY)).toBeTruthy();
    const first = readThreadRestore(store);
    expect(first).toMatchObject({ question: "q", agent: "revenue-agent", model: "gpt-5.6-luna", governedOnly: false });
    // Read-once: a plain refresh must not re-restore.
    expect(store.getItem(THREAD_RESTORE_KEY)).toBeNull();
    expect(readThreadRestore(store)).toBeNull();
  });

  it("persists and restores the assistant trust provenance allowlist", () => {
    const store = memoryStore();
    const bundle = {
      bundle_id: "cb-123",
      resolution: { status: "governed", warnings: [] },
      context_authority: { path: "contexts/revenue", commit_sha: "abc123" },
      linked_evidence: { conflicts: [] },
    };
    const result = {
      bundle_id: "cb-123",
      agent_label: "Revenue agent",
      provider: "openai",
      model: "gpt-5.6-luna",
      agent_config: { policy_result: "allowed", secret: "do-not-store" },
      trace: [{ label: "GOVERNED" }],
      sql: { sql: "SELECT secret FROM internal_table" },
    };
    saveThreadTurn(store, {
      id: "t-trust",
      question: "what is revenue",
      answer: "Revenue is governed.",
      createdAt: 1,
      agent: "revenue-agent",
      model: "gpt-5.6-luna",
      governedOnly: true,
      messages: [
        { id: "u1", role: "user", content: "what is revenue", createdAt: 1 },
        { id: "a1", role: "assistant", content: "Revenue is governed.", createdAt: 2, bundle, result },
      ],
    });
    const raw = store.getItem(THREADS_KEY);
    expect(raw).toContain("cb-123");
    expect(raw).toContain("abc123");
    expect(raw).toContain("gpt-5.6-luna");
    expect(raw).not.toContain("do-not-store");
    expect(raw).not.toContain("SELECT secret");

    const record = JSON.parse(raw)[0];
    const assistant = record.messages.find((message) => message.role === "assistant");
    expect(assistant.result).toMatchObject({ bundle_id: "cb-123", provider: "openai", model: "gpt-5.6-luna" });
    expect(assistant.result.agent_config).toEqual({ policy_result: "allowed" });
    expect(assistant.bundle.context_authority.commit_sha).toBe("abc123");
  });
});
