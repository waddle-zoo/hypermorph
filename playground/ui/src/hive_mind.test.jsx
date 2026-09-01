import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { HiveMindGraph, redactForPersist } from "./hive_mind.jsx";

const LONG = "finance-with-a-very-long-descriptive-domain-name";

const LARGE_WALK = {
  root: { id: "root:default", workspace: "default" },
  domains: Array.from({ length: 18 }, (_, index) => ({
    domain: `domain-${index}`,
    available: true,
    pointers: { source_id: `src-${index}`, context_doc: `context-${index}.md`, approved_sources: [] },
  })),
  edges: Array.from({ length: 18 }, (_, index) => ({
    from: "root:default",
    to: `domain:domain-${index}`,
    relation: "catalog_contains",
    evidence: "catalog",
  })),
};

const WALK = {
  result_kind: "navigation",
  schema_version: 24,
  start: "root:default",
  root: { id: "root:default", kind: "hive_mind_root", workspace: "default" },
  domains: [
    {
      domain: "revenue",
      title: "Recognized revenue",
      available: true,
      pointers: {
        source_id: "src-rev",
        repository: "https://github.com/acme/ctx",
        snapshot_id: "snap-rev",
        commit_sha: "c-rev",
        context_doc: "context.md",
        approved_sources: ["table:pg:orders"],
      },
    },
    {
      domain: "supply_chain",
      title: "Supply chain",
      available: true,
      pointers: {
        source_id: "src-supply",
        repository: "git@host/supply",
        snapshot_id: "snap-supply",
        commit_sha: "c-supply",
        context_doc: "supply.md",
        approved_sources: [],
      },
    },
    {
      domain: LONG,
      available: true,
      pointers: {
        source_id: "src-fin",
        repository: "git@host/fin",
        snapshot_id: "snap-fin",
        commit_sha: "c-fin",
        context_doc: "finance.md",
        approved_sources: [],
      },
    },
    { domain: "secret", available: false, exclusion: "acl" },
  ],
  edges: [
    { from: "root:default", to: "domain:revenue", relation: "catalog_contains", evidence: "system" },
    { from: "root:default", to: "domain:supply_chain", relation: "catalog_contains", evidence: "system" },
    { from: "domain:revenue", to: `domain:${LONG}`, relation: "contains", evidence: "git" },
  ],
  warnings: [{ code: "expansion_acl_excluded", message: "'secret' is not visible to this caller" }],
};

function makeRequest(overrides = {}) {
  const calls = [];
  const request = vi.fn((path, payload, method = "POST") => {
    calls.push({ path, payload, method });
    if (path in overrides) return overrides[path](payload);
    if (path === "/v0/expand_analytics_context") return Promise.resolve(WALK);
    return Promise.resolve({});
  });
  return { request, calls };
}

async function renderGraph(overrides = {}) {
  const { request, calls } = makeRequest(overrides);
  render(<HiveMindGraph requestJson={request} />);
  await waitFor(() => expect(screen.getByRole("region", { name: /hive-mind knowledge graph/i })).toBeInTheDocument());
  return { request, calls };
}

describe("HiveMindGraph", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("auto-loads a visual graph from the workspace root", async () => {
    const { calls } = await renderGraph();
    const expandCall = calls.find((call) => call.path === "/v0/expand_analytics_context");
    expect(expandCall.payload).toEqual({
      query: "explore",
      from_root: true,
      max_hops: 4,
      max_components: 50,
      context_budget: 24000,
    });
    expect(screen.getByRole("region", { name: /hive-mind knowledge graph/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Domain: revenue/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Domain: supply_chain/i })).toBeInTheDocument();
    expect(screen.getByText(/nodes · .* edges/)).toBeInTheDocument();
    expect(screen.getByText(/'secret' is not visible to this caller/)).toBeInTheDocument();
  });

  it("shows the root's high-level domains and derived document/source lineage", async () => {
    await renderGraph();
    expect(screen.getByRole("button", { name: /Hive-Mind root: Hive-Mind root/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Context document: context\.md/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approved source: table:pg:orders/i })).toBeInTheDocument();
    // Long names remain available to the human and assistive technology rather than
    // being silently sliced to fit a card.
    expect(screen.getByRole("button", { name: new RegExp(LONG) })).toBeInTheDocument();
  });

  it("clicking a domain opens only its selected-node details", async () => {
    await renderGraph();
    fireEvent.click(screen.getByRole("button", { name: /Domain: revenue/i }));
    expect(screen.getByRole("heading", { name: "revenue", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("https://github.com/acme/ctx")).toBeInTheDocument();
    expect(screen.getByText("c-rev")).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: /selected node details/i })).getAllByText("context.md").length).toBeGreaterThan(0);
    expect(screen.getByText("Connections")).toBeInTheDocument();
    expect(screen.queryByText(/Retrieve context/i)).toBeNull();
    expect(screen.queryByText(/Ask in live chat/i)).toBeNull();
  });

  it("clicking a document or source shows its lineage metadata", async () => {
    await renderGraph();
    fireEvent.click(screen.getByRole("button", { name: /Context document: context\.md/i }));
    expect(screen.getByRole("heading", { name: "context.md", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("src-rev")).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: /selected node details/i })).getAllByText("revenue").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Approved source: table:pg:orders/i }));
    expect(screen.getByRole("heading", { name: "table:pg:orders", level: 2 })).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: /selected node details/i })).getAllByText("Approved source").length).toBeGreaterThan(0);
  });

  it("supports keyboard navigation for graph nodes", async () => {
    await renderGraph();
    fireEvent.keyDown(screen.getByRole("button", { name: /Domain: supply_chain/i }), { key: "Enter" });
    expect(screen.getByRole("heading", { name: "supply_chain", level: 2 })).toBeInTheDocument();
  });

  it("wraps large depth levels into bounded lanes and scrolls focused nodes into view", async () => {
    await renderGraph({ "/v0/expand_analytics_context": () => Promise.resolve(LARGE_WALK) });
    const region = screen.getByRole("region", { name: /hive-mind knowledge graph/i });
    const domainNodes = screen.getAllByRole("button", { name: /Domain: domain-/i });
    expect(new Set(domainNodes.map((node) => node.style.left)).size).toBeGreaterThan(1);
    expect(new Set(domainNodes.map((node) => node.style.top)).size).toBeLessThanOrEqual(6);
    expect(Number.parseFloat(region.firstElementChild.style.height)).toBeLessThanOrEqual(700);
    expect(region).toHaveAttribute("tabindex", "0");

    const distantNode = domainNodes.at(-1);
    distantNode.scrollIntoView = vi.fn();
    fireEvent.focus(distantNode);
    expect(distantNode.scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
  });

  it("keeps a large graph navigable with bounded zoom controls", async () => {
    await renderGraph();
    const region = screen.getByRole("region", { name: /hive-mind knowledge graph/i });
    expect(screen.getByRole("group", { name: "Graph view controls" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByLabelText("Graph zoom 90 percent")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fit graph" }));
    expect(Number.parseFloat(region.firstElementChild.style.width)).toBeLessThan(1060);
  });

  it("keeps edge evidence visible without presenting navigation as authority", async () => {
    await renderGraph();
    fireEvent.click(screen.getByRole("button", { name: /Domain: revenue/i }));
    const inspector = screen.getByRole("complementary", { name: /selected node details/i });
    expect(within(inspector).getByText("catalog_contains · evidence: system")).toBeInTheDocument();
    expect(within(inspector).queryByText(/governed authority/i)).toBeNull();
  });

  it("renders an unavailable domain as a locked graph node without source details", async () => {
    await renderGraph();
    fireEvent.click(screen.getByRole("button", { name: /Unavailable domain: secret/i }));
    expect(within(screen.getByRole("complementary", { name: /selected node details/i })).getAllByText(/not visible to you/i).length).toBeGreaterThan(0);
    expect(screen.queryByText("src-secret")).toBeNull();
  });

  it("does not call search_knowledge: the graph is navigation, not a second search surface", async () => {
    const { calls } = await renderGraph();
    expect(calls.find((call) => call.path === "/v0/search_knowledge")).toBeUndefined();
  });

  it("redacts credential-bearing repository data before it reaches the graph DOM", async () => {
    const privateWalk = {
      ...WALK,
      domains: WALK.domains.map((entry) => entry.domain === "revenue"
        ? { ...entry, pointers: { ...entry.pointers, repository: "https://alice:ghp_GRAPHSECRET@github.com/acme/ctx" } }
        : entry),
    };
    await renderGraph({ "/v0/expand_analytics_context": () => Promise.resolve(privateWalk) });
    expect(document.body.textContent).not.toContain("ghp_GRAPHSECRET");
    expect(document.body.textContent).not.toContain("alice:");
  });

  it("redacts a credential-bearing graph error before it reaches the DOM", async () => {
    const { request } = makeRequest({
      "/v0/expand_analytics_context": () => Promise.reject(new Error("load failed for https://bob:ghp_ERRSECRET@host/repo")),
    });
    render(<HiveMindGraph requestJson={request} />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("load failed");
    expect(document.body.textContent).not.toContain("ghp_ERRSECRET");
    expect(document.body.textContent).not.toContain("bob:");
  });

  it("redactForPersist strips URL userinfo from any state that would be persisted", () => {
    const redacted = redactForPersist({
      nested: { repository: "https://alice:ghp_SECRET@github.com/acme/ctx" },
    });
    expect(JSON.stringify(redacted)).not.toContain("ghp_SECRET");
    expect(JSON.stringify(redacted)).not.toContain("alice:");
  });
});
