import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { WritebackTargetsPanel } from "./writeback_targets.jsx";

// Two targets the backend would serve: a keyed 'revenue' target with an env_ref
// secret NAME, and the default (catch-all). Neither carries a secret VALUE --
// the backend never sends one; only references (token_ref name / app_id) and a
// token_set flag.
const TARGETS = [
  {
    id: "wbtgt_rev",
    routing_key: "revenue",
    is_default: false,
    enabled: true,
    repository: "https://github.com/acme/context",
    base_ref: "main",
    manifest_path: "domains/revenue",
    reviewer_routing: "alice, bob",
    token_source: "env_ref",
    token_ref: "HYPERSET_WB_TOKEN",
    app_id: null,
    token_set: true,
    test_result: "ready",
  },
  {
    id: "wbtgt_default",
    routing_key: null,
    is_default: true,
    enabled: true,
    repository: "/srv/context",
    base_ref: "main",
    manifest_path: "domains/revenue",
    token_source: "env_ref",
    token_ref: null,
    app_id: null,
    token_set: false,
    test_result: null,
  },
];

// A fake requestJson that records every call and answers list/mutations. It
// stands in for the real admin fetch so a test asserts the exact endpoint,
// method, and payload each button hits.
function makeRequest(overrides = {}) {
  const calls = [];
  const request = vi.fn((path, payload, method = "POST") => {
    calls.push({ path, payload, method });
    if (path === "/v0/review/writeback-targets" && method === "GET") {
      return Promise.resolve({ targets: TARGETS });
    }
    if (path in overrides) return overrides[path](payload);
    return Promise.resolve({});
  });
  return { request, calls };
}

async function renderPanel(overrides) {
  const { request, calls } = makeRequest(overrides);
  render(<WritebackTargetsPanel requestJson={request} />);
  await screen.findByText(/acme\/context@main/);
  return { request, calls };
}

describe("WritebackTargetsPanel", () => {
  it("lists targets with their pointer, routing key, and a secret reference only", async () => {
    await renderPanel();
    expect(screen.getByText(/revenue → https:\/\/github.com\/acme\/context@main/)).toBeInTheDocument();
    expect(screen.getByText(/\(default catch-all\) → \/srv\/context@main/)).toBeInTheDocument();
    // The secret is shown as a NAME reference, never a value.
    expect(screen.getByText("env: HYPERSET_WB_TOKEN")).toBeInTheDocument();
    // The routed reviewers are shown; an unrouted target reads as needs-routing
    // (the review card's needs-routing link brings an admin here to set them).
    expect(screen.getByText("alice, bob")).toBeInTheDocument();
    expect(screen.getByText(/none \(needs routing\)/)).toBeInTheDocument();
  });

  // Even if a stray secret VALUE rode along on a record (in any token-source
  // shape), the panel reads only reference fields, so nothing secret can reach
  // the DOM. Covered across ALL THREE sources so no branch renders a value.
  const SECRET_LEAKS = [
    { name: "env_ref", extra: { token_source: "env_ref", token_ref: "HYPERSET_WB_TOKEN", token: "ghp_LEAKED_env" }, leaks: ["ghp_LEAKED_env"] },
    { name: "encrypted", extra: { token_source: "encrypted", token_set: true, token: "ghp_LEAKED_pat", token_ciphertext: "CIPHERTEXT_LEAK", token_nonce: "NONCE_LEAK" }, leaks: ["ghp_LEAKED_pat", "CIPHERTEXT_LEAK", "NONCE_LEAK"] },
    { name: "github_app", extra: { token_source: "github_app", app_id: 42, token_set: true, app_private_key: "-----BEGIN RSA PRIVATE KEY LEAK-----", app_key_ciphertext: "APPKEY_CIPHER_LEAK" }, leaks: ["PRIVATE KEY LEAK", "APPKEY_CIPHER_LEAK"] },
  ];
  it.each(SECRET_LEAKS)("renders no secret VALUE for a $name target, only references", async ({ extra, leaks }) => {
    const withStray = [{ ...TARGETS[0], ...extra }];
    const { request } = makeRequest({});
    request.mockImplementation((path, payload, method = "POST") =>
      path === "/v0/review/writeback-targets" && method === "GET"
        ? Promise.resolve({ targets: withStray })
        : Promise.resolve({})
    );
    render(<WritebackTargetsPanel requestJson={request} />);
    await screen.findByText(/acme\/context@main/);
    for (const leak of leaks) {
      expect(document.body.textContent).not.toContain(leak);
    }
  });

  it("Test target calls /test and shows the probe result with no PR", async () => {
    const probe = { target_id: "wbtgt_rev", status: "blocked", reason: "unreachable host", recovery: "check the URL" };
    const { calls } = await renderPanel({
      "/v0/review/writeback-targets/test": () => Promise.resolve({ probe }),
    });
    const row = screen.getByText(/revenue → https/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Test target" }));
    const status = await within(row).findByRole("status");
    expect(status).toHaveTextContent(/blocked — unreachable host/);
    // The probe hit the test endpoint with the target id...
    expect(calls).toContainEqual({
      path: "/v0/review/writeback-targets/test",
      payload: { id: "wbtgt_rev" },
      method: "POST",
    });
    // ...and opened NO PR: nothing in the surface links to or mentions a pull request.
    expect(document.body.textContent.toLowerCase()).not.toContain("pull request");
    expect(document.querySelector("a[href*='/pull/']")).toBeNull();
  });

  it("Disable hits /enable(enabled=false) and Delete hits /delete for a keyed target", async () => {
    const { calls } = await renderPanel();
    const row = screen.getByText(/revenue → https/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/review/writeback-targets/enable",
        payload: { id: "wbtgt_rev", enabled: false },
        method: "POST",
      })
    );
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/review/writeback-targets/delete",
        payload: { id: "wbtgt_rev" },
        method: "POST",
      })
    );
  });

  it("the default/catch-all target is disable-only: no Delete and no Edit", async () => {
    await renderPanel();
    const defaultRow = screen.getByText(/\(default catch-all\)/).closest("article");
    expect(within(defaultRow).queryByRole("button", { name: "Delete" })).toBeNull();
    expect(within(defaultRow).queryByRole("button", { name: "Edit" })).toBeNull();
    // It can still be disabled and probed.
    expect(within(defaultRow).getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(within(defaultRow).getByRole("button", { name: "Test target" })).toBeInTheDocument();
  });

  it("the DEFAULT target's reviewer routing is editable and posts to writeback-config", async () => {
    // A default-routed proposal (the common fallback) must be routable; without a
    // reviewer editor on the default its needs-routing action was dead (#435
    // round 3). The default has no keyed Edit/Delete, but 'Set reviewers' updates
    // ONLY reviewer_routing via the config endpoint, preserving its config and
    // secret references (no secret value sent, routing_key stays null).
    const { calls } = await renderPanel();
    const defaultRow = screen.getByText(/\(default catch-all\)/).closest("article");
    fireEvent.click(within(defaultRow).getByRole("button", { name: "Set reviewers" }));
    fireEvent.change(within(defaultRow).getByLabelText("default reviewer routing"), { target: { value: "alice, bob" } });
    fireEvent.click(within(defaultRow).getByRole("button", { name: "Save reviewers" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/review/writeback-config",
        payload: {
          repository: "/srv/context",
          base_ref: "main",
          manifest_path: "domains/revenue",
          token_source: "env_ref",
          reviewer_routing: "alice, bob",
          token_ref: "",
        },
        method: "POST",
      })
    );
    // It never deletes or re-keys the default (identity preserved).
    expect(calls.some((c) => c.path === "/v0/review/writeback-targets/delete")).toBe(false);
    expect(calls.find((c) => c.path === "/v0/review/writeback-config" && c.payload.reviewer_routing).payload.routing_key).toBeUndefined();
  });

  it("Add posts a keyed target to the write-back-targets endpoint", async () => {
    const { calls } = await renderPanel();
    fireEvent.change(screen.getByLabelText("routing key"), { target: { value: "marketing" } });
    fireEvent.change(screen.getByLabelText("repository"), { target: { value: "/srv/mkt" } });
    fireEvent.change(screen.getByLabelText("manifest path"), { target: { value: "domains/marketing" } });
    fireEvent.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/review/writeback-targets",
        payload: {
          routing_key: "marketing",
          repository: "/srv/mkt",
          base_ref: "main",
          manifest_path: "domains/marketing",
          reviewer_routing: "",
          token_source: "env_ref",
          token_ref: "",
        },
        method: "POST",
      })
    );
  });

  it("Add includes reviewer_routing in the payload so per-target reviewers are configurable", async () => {
    // The needs-routing affordance on the review card links here; without this
    // field the admin form could not set reviewers and that link was dead (#435
    // adversary bounce). The control renders and its value is POSTed.
    const { calls } = await renderPanel();
    expect(screen.getByLabelText("reviewer routing")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("routing key"), { target: { value: "marketing" } });
    fireEvent.change(screen.getByLabelText("repository"), { target: { value: "/srv/mkt" } });
    fireEvent.change(screen.getByLabelText("manifest path"), { target: { value: "domains/marketing" } });
    fireEvent.change(screen.getByLabelText("reviewer routing"), { target: { value: "carol, dave" } });
    fireEvent.click(screen.getByRole("button", { name: "Add target" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/review/writeback-targets",
        payload: {
          routing_key: "marketing",
          repository: "/srv/mkt",
          base_ref: "main",
          manifest_path: "domains/marketing",
          reviewer_routing: "carol, dave",
          token_source: "env_ref",
          token_ref: "",
        },
        method: "POST",
      })
    );
  });
});
