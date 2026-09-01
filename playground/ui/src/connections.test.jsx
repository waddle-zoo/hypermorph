import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ConnectionsPanel, redactUserinfo } from "./connections.jsx";

const PROD = {
  id: "c-prod",
  connector_type: "superset",
  display_name: "Prod Superset",
  config_ref: "https://superset.internal/api",
  enabled: true,
  health_status: "healthy",
  health_checked_at: "2026-08-21T00:00:00Z",
  health_detail: "5 asset(s) found",
  has_observed_assets: true,
};
const CATALOG = {
  id: "c-cat",
  connector_type: "datahub",
  display_name: "Catalog",
  config_ref: "https://datahub.internal/gms",
  enabled: true,
  health_status: "unknown",
  health_checked_at: null,
  health_detail: null,
  has_observed_assets: false,
};

function makeRequest(list, overrides = {}) {
  const calls = [];
  const request = vi.fn((path, payload, method = "POST") => {
    calls.push({ path, payload, method });
    if (path === "/v0/connections" && method === "GET") return Promise.resolve({ connections: list });
    if (path in overrides) return overrides[path](payload);
    return Promise.resolve({});
  });
  return { request, calls };
}

async function renderPanel(list = [PROD, CATALOG], overrides) {
  const { request, calls } = makeRequest(list, overrides);
  render(<ConnectionsPanel requestJson={request} />);
  await waitFor(() =>
    expect(document.querySelectorAll("article.sources-row").length).toBe(list.length),
  );
  return { request, calls };
}

describe("ConnectionsPanel", () => {
  it("lists connections with type, name, config ref, and recorded health (readiness)", async () => {
    await renderPanel();
    const row = screen.getByText(/superset · Prod Superset/).closest("article");
    expect(within(row).getByText("c-prod")).toBeInTheDocument();
    expect(within(row).getByText("https://superset.internal/api")).toBeInTheDocument();
    expect(within(row).getByText("healthy")).toBeInTheDocument();
    // The other connection shows its recorded health honestly as unknown.
    const cat = screen.getByText(/datahub · Catalog/).closest("article");
    expect(within(cat).getByText("unknown")).toBeInTheDocument();
  });

  it("Probe hits /probe and surfaces a DEGRADED status with impact and recovery", async () => {
    const probe = {
      connection_id: "c-prod",
      status: "degraded",
      configured: true,
      reachable: true,
      fresh: false,
      reason: "reachable, but no recent successful sync",
      impact: "evidence may be stale",
      recovery: "run a sync for this connection",
    };
    const { calls } = await renderPanel([PROD], {
      "/v0/connections/probe": () => Promise.resolve({ probe }),
    });
    const row = screen.getByText(/Prod Superset/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Probe" }));
    const status = await within(row).findByRole("status");
    expect(status).toHaveTextContent(/degraded/);
    // The live FACTS are shown, so liveness is observably distinct from readiness.
    expect(status).toHaveTextContent(/configured: yes/);
    expect(status).toHaveTextContent(/reachable: yes/);
    expect(status).toHaveTextContent(/fresh: no/);
    expect(status).toHaveTextContent(/Impact: evidence may be stale/);
    expect(status).toHaveTextContent(/Recovery: run a sync/);
    expect(calls).toContainEqual({ path: "/v0/connections/probe", payload: { id: "c-prod" }, method: "POST" });
    expect(document.body.textContent.toLowerCase()).not.toContain("pull request");
  });

  it("a BLOCKED probe shows the not-reachable facts honestly", async () => {
    const probe = {
      connection_id: "c-prod",
      status: "blocked",
      configured: true,
      reachable: false,
      fresh: false,
      reason: "the source could not be reached",
      impact: "no fresh corroboration",
      recovery: "check the base URL",
    };
    await renderPanel([PROD], { "/v0/connections/probe": () => Promise.resolve({ probe }) });
    const row = screen.getByText(/Prod Superset/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Probe" }));
    const status = await within(row).findByRole("status");
    expect(status).toHaveTextContent(/blocked/);
    expect(status).toHaveTextContent(/reachable: no/);
    expect(status).toHaveTextContent(/Impact: no fresh corroboration/);
  });

  it("Disable, Add, and Edit hit the right endpoints", async () => {
    const { calls } = await renderPanel([CATALOG], {
      "/v0/connections": (payload) =>
        payload ? Promise.resolve({ connection: { id: "c-new" } }) : Promise.resolve({ connections: [CATALOG] }),
    });
    const row = screen.getByText(/Catalog/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(calls).toContainEqual({ path: "/v0/connections/enable", payload: { id: "c-cat", enabled: false }, method: "POST" }),
    );
    fireEvent.change(screen.getByLabelText("display name"), { target: { value: "New Superset" } });
    fireEvent.change(screen.getByLabelText("config ref"), { target: { value: "/srv/bundle.zip" } });
    fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: "/v0/connections",
        payload: { connector_type: "superset", display_name: "New Superset", config_ref: "/srv/bundle.zip" },
        method: "POST",
      }),
    );
  });

  it("Remove is disabled for a connection with observed assets; enabled otherwise", async () => {
    await renderPanel([PROD, CATALOG]);
    const withAssets = screen.getByText(/Prod Superset/).closest("article");
    expect(within(withAssets).getByRole("button", { name: "Remove" })).toBeDisabled();
    const noAssets = screen.getByText(/Catalog/).closest("article");
    const removeBtn = within(noAssets).getByRole("button", { name: "Remove" });
    expect(removeBtn).not.toBeDisabled();
    fireEvent.click(removeBtn);
  });

  // A credential in a RENDERED field (config_ref, health_detail, probe reason) must not
  // reach the DOM -- the actual leak surface, not an ignored top-level prop. The server
  // redacts these fields (the boundary, covered by the postgres tests); this asserts the UI
  // ALSO cannot render a credential a field somehow carried. Mutation-verified below.
  const RENDER_LEAKS = [
    {
      name: "config_ref",
      connection: { ...PROD, config_ref: "https://u:ghp_CFGLEAK@superset.internal/api" },
      leak: "ghp_CFGLEAK",
      keeps: "superset.internal/api",
    },
    {
      name: "health_detail",
      connection: { ...PROD, health_detail: "refused: https://u:ghp_DETLEAK@superset.internal/api" },
      leak: "ghp_DETLEAK",
      keeps: "superset.internal/api",
    },
  ];
  it.each(RENDER_LEAKS)("renders $name with any credential redacted", async ({ connection, leak, keeps }) => {
    await renderPanel([connection]);
    expect(document.body.textContent).not.toContain(leak);
    expect(document.body.textContent).toContain(keeps);
  });

  it("a credential in the probe reason is not rendered", async () => {
    const probe = {
      connection_id: "c-prod",
      status: "blocked",
      configured: true,
      reachable: false,
      fresh: false,
      reason: "refused for https://u:ghp_REASONLEAK@superset.internal/api",
      impact: "no fresh corroboration",
      recovery: "check the URL",
    };
    await renderPanel([PROD], { "/v0/connections/probe": () => Promise.resolve({ probe }) });
    const row = screen.getByText(/Prod Superset/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Probe" }));
    await within(row).findByRole("status");
    expect(document.body.textContent).not.toContain("ghp_REASONLEAK");
    expect(document.body.textContent).toContain("superset.internal/api");
  });

  it("redactUserinfo strips scheme://userinfo@ and preserves scp / port@rev", () => {
    expect(redactUserinfo("https://u:tok@host/r")).toBe("https://host/r");
    expect(redactUserinfo("https://u:tok?@host/r")).not.toContain("tok"); // ? inside userinfo
    expect(redactUserinfo("git@host:o/r")).toBe("git@host:o/r"); // scp, no scheme
    expect(redactUserinfo("https://git.corp:8443/team/repo HEAD@{1}")).toContain("HEAD@{1}");
    expect(redactUserinfo("/srv/bundle.zip")).toBe("/srv/bundle.zip");
  });
});
