import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReviewPage, reviewIsStale, filterReviewTasks } from "./main.jsx";

const ME = "auth0|me@https://issuer.example";
const OTHER = "auth0|other@https://issuer.example";
const OLD = "2020-01-01T00:00:00Z";
const NOW_MS = Date.parse("2026-08-24T00:00:00Z");

function qtask(id, { status = "open", priority = 2, assignee = null, term = id, created_at = OLD } = {}) {
  return {
    id,
    status,
    priority,
    assignee,
    created_at,
    proposal_payload: { domain: "revenue", definition: { definitions: [{ term, statement: "s" }] } },
  };
}

describe("reviewIsStale", () => {
  it("is stale only for an open/in_progress task older than the window", () => {
    expect(reviewIsStale(qtask("a", { status: "open", created_at: OLD }), NOW_MS)).toBe(true);
    expect(reviewIsStale(qtask("b", { status: "in_progress", created_at: OLD }), NOW_MS)).toBe(true);
    // Recent open task is not stale.
    const recent = new Date(NOW_MS - 86400000).toISOString();
    expect(reviewIsStale(qtask("c", { status: "open", created_at: recent }), NOW_MS)).toBe(false);
    // Resolved/dismissed never stale, even when old.
    expect(reviewIsStale(qtask("d", { status: "resolved", created_at: OLD }), NOW_MS)).toBe(false);
    // Missing/unparseable date is not stale (never guess).
    expect(reviewIsStale({ status: "open", created_at: null }, NOW_MS)).toBe(false);
  });
});

describe("filterReviewTasks", () => {
  const tasks = [
    qtask("open_p0", { status: "open", priority: 0, assignee: null }),
    qtask("mine_resolved", { status: "resolved", priority: 2, assignee: ME }),
    qtask("theirs_open", { status: "open", priority: 2, assignee: OTHER }),
  ];

  it("filters by status", () => {
    expect(filterReviewTasks(tasks, { status: "resolved" }, ME, NOW_MS).map((t) => t.id)).toEqual([
      "mine_resolved",
    ]);
  });
  it("filters by urgency (priority), coercing string and number", () => {
    expect(filterReviewTasks(tasks, { priority: "0" }, ME, NOW_MS).map((t) => t.id)).toEqual([
      "open_p0",
    ]);
    expect(filterReviewTasks(tasks, { priority: 0 }, ME, NOW_MS).map((t) => t.id)).toEqual([
      "open_p0",
    ]);
  });
  it("mineOnly matches my tasks, and NOTHING without an identity", () => {
    expect(filterReviewTasks(tasks, { mineOnly: true }, ME, NOW_MS).map((t) => t.id)).toEqual([
      "mine_resolved",
    ]);
    expect(filterReviewTasks(tasks, { mineOnly: true }, "", NOW_MS)).toEqual([]);
  });
  it("unassignedOnly matches only owner-less gaps", () => {
    expect(filterReviewTasks(tasks, { unassignedOnly: true }, ME, NOW_MS).map((t) => t.id)).toEqual([
      "open_p0",
    ]);
  });
  it("staleOnly reuses reviewIsStale", () => {
    // All are OLD, but only open/in_progress are stale => the resolved one drops out.
    expect(filterReviewTasks(tasks, { staleOnly: true }, ME, NOW_MS).map((t) => t.id).sort()).toEqual(
      ["open_p0", "theirs_open"],
    );
  });
  it("combines predicates (AND)", () => {
    expect(
      filterReviewTasks(tasks, { status: "open", priority: "2", unassignedOnly: true }, ME, NOW_MS),
    ).toEqual([]); // theirs_open is open+p2 but assigned; open_p0 is p0
  });
});

function makeRequest(tasksList) {
  const state = { tasks: tasksList };
  return vi.fn((path, payload, method = "POST") => {
    if (path === "/v0/list_review_tasks") return Promise.resolve({ tasks: state.tasks });
    if (path === "/v0/review/writeback-config") return Promise.resolve({ config: null });
    if (path === "/v0/review/whoami") return Promise.resolve({ identity: ME });
    return Promise.resolve({});
  });
}

describe("ReviewPage filter controls", () => {
  const recent = new Date(Date.now() - 86400000).toISOString();
  const tasks = [
    qtask("churn_stale", { status: "open", priority: 0, assignee: null, created_at: OLD }),
    qtask("churn_fresh", { status: "in_progress", priority: 2, assignee: OTHER, created_at: recent }),
  ];

  it("does not disable Propose when the optional default is absent", async () => {
    const request = makeRequest(tasks);
    render(<ReviewPage request={request} />);
    await waitFor(() => expect(screen.getByText("churn_stale")).toBeTruthy());
    const propose = screen.getAllByRole("button", { name: "Propose to Git →" });
    expect(propose[0]).not.toBeDisabled();
    fireEvent.click(propose[0]);
    await waitFor(() => expect(request).toHaveBeenCalledWith("/v0/propose_review_to_git", { task_id: "churn_stale" }));
  });

  it("keeps the intro and wrap-ready filter group as separate responsive regions", async () => {
    render(<ReviewPage request={makeRequest(tasks)} />);
    await waitFor(() => expect(screen.getByText("churn_stale")).toBeTruthy());
    const heading = screen.getByRole("heading", { name: "Context review" }).closest(".review-page-heading");
    const filters = screen.getByRole("group", { name: "Review queue filters" });
    expect(heading?.querySelector(":scope > .review-page-intro")).toBeTruthy();
    expect(heading?.querySelector(":scope > .review-page-actions")).toBe(filters);
  });

  it("narrows the queue by status", async () => {
    render(<ReviewPage request={makeRequest(tasks)} />);
    await waitFor(() => expect(screen.getByText("churn_stale")).toBeTruthy());
    expect(screen.getByText("churn_fresh")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "in_progress" } });
    await waitFor(() => expect(screen.queryByText("churn_stale")).toBeNull());
    expect(screen.getByText("churn_fresh")).toBeTruthy();
  });

  it("narrows by urgency (priority)", async () => {
    render(<ReviewPage request={makeRequest(tasks)} />);
    await waitFor(() => expect(screen.getByText("churn_stale")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Urgency"), { target: { value: "0" } });
    await waitFor(() => expect(screen.queryByText("churn_fresh")).toBeNull());
    expect(screen.getByText("churn_stale")).toBeTruthy();
  });

  it("narrows to stale and to unassigned", async () => {
    render(<ReviewPage request={makeRequest(tasks)} />);
    await waitFor(() => expect(screen.getByText("churn_stale")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Stale"));
    await waitFor(() => expect(screen.queryByText("churn_fresh")).toBeNull());
    expect(screen.getByText("churn_stale")).toBeTruthy(); // open + old => stale
    fireEvent.click(screen.getByLabelText("Stale")); // clear
    fireEvent.click(screen.getByLabelText("Unassigned"));
    await waitFor(() => expect(screen.queryByText("churn_fresh")).toBeNull());
    expect(screen.getByText("churn_stale")).toBeTruthy(); // the only owner-less gap
  });
});
