import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReviewTaskItem, ReviewPage, reviewOwner, assignedToMe, reviewSuggestedOwner, reviewSuggestionSummary } from "./main.jsx";

const ME = "auth0|me@https://issuer.example";
const OTHER = "auth0|other@https://issuer.example";
const SUGGESTION_SUMMARY = "Most recent prior reviewer in this governed domain.";

function task(id, assignee = null, term = "churn", suggested = null) {
  const t = {
    id,
    assignee,
    proposal_payload: {
      domain: "revenue",
      definition: { definitions: [{ term, statement: "customers lost" }] },
    },
  };
  if (suggested !== null) {
    t.suggested_assignee = suggested;
    // The server rides a rationale with every suggestion (hy-38mk8 r2).
    t.suggested_assignee_rationale = { signal: "prior_in_domain_reviewer", summary: SUGGESTION_SUMMARY, assist: true };
  }
  return t;
}

const NOOP = {
  onPropose: () => {},
  onEdit: () => {},
  onUndo: () => {},
  busy: false,
  canPropose: false,
};

describe("review owner helpers", () => {
  it("redacts URL userinfo from the owner at the data boundary", () => {
    // A legacy credential-shaped assignee never reaches the DOM unscrubbed (hy-6tsw9).
    expect(reviewOwner({ assignee: "https://user:supersecret@host/x" })).toBe("https://host/x");
    expect(reviewOwner({ assignee: null })).toBeNull();
    expect(reviewOwner({})).toBeNull();
  });

  it("'assigned to me' matches only my identity, and nothing without one", () => {
    const tasks = [task("a", ME), task("b", OTHER), task("c", null)];
    expect(assignedToMe(tasks, ME).map((t) => t.id)).toEqual(["a"]);
    // No identity yet => matches nothing rather than guessing.
    expect(assignedToMe(tasks, "")).toEqual([]);
  });

  it("reviewSuggestedOwner reads the hint and redacts it, null when absent", () => {
    expect(reviewSuggestedOwner(task("a", null, "churn", OTHER))).toBe(OTHER);
    expect(reviewSuggestedOwner(task("b"))).toBeNull();
    // A credential-shaped hint is scrubbed at the boundary (hy-38mk8, hy-6tsw9).
    expect(reviewSuggestedOwner({ suggested_assignee: "https://user:supersecret@host/x" })).toBe(
      "https://host/x",
    );
  });

  it("reviewSuggestionSummary reads the SERVER rationale, with a generic fallback", () => {
    // The served rationale summary is used, not a hard-coded UI string (hy-38mk8 r2).
    expect(reviewSuggestionSummary(task("a", null, "churn", OTHER))).toBe(SUGGESTION_SUMMARY);
    // An older server that omits the rationale still gets a sane, generic line.
    expect(reviewSuggestionSummary({ suggested_assignee: OTHER })).toContain("confirm");
  });
});

describe("ReviewTaskItem owner suggestion (hy-38mk8)", () => {
  it("an unassigned task with a hint shows 'Suggested: <id>'", () => {
    render(
      <ReviewTaskItem task={task("t1", null, "churn", OTHER)} myIdentity={ME} onAssignSelf={() => {}} onUnassign={() => {}} {...NOOP} />,
    );
    expect(screen.getByText(`Suggested: ${OTHER}`)).toBeTruthy();
    // The chip's tooltip is the SERVER's rationale summary (hy-38mk8 r2).
    expect(screen.getByText(`Suggested: ${OTHER}`).getAttribute("title")).toBe(SUGGESTION_SUMMARY);
  });

  it("an OWNED task shows no suggestion even if the field is present", () => {
    render(
      <ReviewTaskItem task={task("t2", ME, "churn", OTHER)} myIdentity={ME} onAssignSelf={() => {}} onUnassign={() => {}} {...NOOP} />,
    );
    expect(screen.queryByText(`Suggested: ${OTHER}`)).toBeNull();
    expect(screen.getByText("Assigned to you")).toBeTruthy();
  });

  it("a credential-shaped hint never reaches the DOM", () => {
    const { container } = render(
      <ReviewTaskItem task={task("t3", null, "churn", "https://user:supersecret@host/x")} myIdentity={ME} onAssignSelf={() => {}} onUnassign={() => {}} {...NOOP} />,
    );
    expect(container.textContent).not.toContain("supersecret");
    expect(container.textContent).toContain("Suggested:");
  });
});

describe("ReviewTaskItem owner + claim controls", () => {
  it("an unassigned task shows Unassigned and an Assign-to-me button", () => {
    const onAssignSelf = vi.fn();
    render(<ReviewTaskItem task={task("t1", null)} myIdentity={ME} onAssignSelf={onAssignSelf} onUnassign={() => {}} {...NOOP} />);
    expect(screen.getByText("Unassigned")).toBeTruthy();
    fireEvent.click(screen.getByText("Assign to me"));
    expect(onAssignSelf).toHaveBeenCalledWith("t1");
  });

  it("a task owned by me shows 'Assigned to you' and an Unassign button", () => {
    const onUnassign = vi.fn();
    render(<ReviewTaskItem task={task("t2", ME)} myIdentity={ME} onAssignSelf={() => {}} onUnassign={onUnassign} {...NOOP} />);
    expect(screen.getByText("Assigned to you")).toBeTruthy();
    expect(screen.queryByText("Assign to me")).toBeNull();
    fireEvent.click(screen.getByText("Unassign"));
    expect(onUnassign).toHaveBeenCalledWith("t2");
  });

  it("a task owned by someone else shows the owner and offers no claim", () => {
    render(<ReviewTaskItem task={task("t3", OTHER)} myIdentity={ME} onAssignSelf={() => {}} onUnassign={() => {}} {...NOOP} />);
    expect(screen.getByText(`Owned by ${OTHER}`)).toBeTruthy();
    expect(screen.queryByText("Assign to me")).toBeNull();
    expect(screen.queryByText("Unassign")).toBeNull();
  });

  it("redacts a credential-shaped owner before it reaches the DOM", () => {
    const { container } = render(<ReviewTaskItem task={task("t4", "https://user:supersecret@host/x")} myIdentity={ME} onAssignSelf={() => {}} onUnassign={() => {}} {...NOOP} />);
    expect(container.textContent).not.toContain("supersecret");
    expect(container.textContent).toContain("host");
  });
});

function makeRequest(tasksList, whoami = ME) {
  const state = { tasks: tasksList.map((t) => ({ ...t })), whoami };
  const calls = [];
  const request = vi.fn((path, payload, method = "POST") => {
    calls.push({ path, payload, method });
    if (path === "/v0/list_review_tasks") return Promise.resolve({ tasks: state.tasks });
    if (path === "/v0/review/writeback-config") return Promise.resolve({ config: null });
    // WHO the server says the caller is -- the sole identity authority (round 2).
    if (path === "/v0/review/whoami") return Promise.resolve({ identity: state.whoami });
    if (path === "/v0/set_review_assignee") {
      const row = state.tasks.find((t) => t.id === payload.task_id);
      row.assignee = payload.assigned ? ME : null;  // the server computes the caller's identity
      return Promise.resolve({ task: row });
    }
    return Promise.resolve({});
  });
  return { request, calls };
}

describe("ReviewPage self-claim + 'assigned to me' filter", () => {
  it("a verified caller claims a task and filters the queue to the tasks the server says are theirs", async () => {
    const { request } = makeRequest([task("mine", null, "churn_mine"), task("theirs", OTHER, "churn_theirs")], ME);
    render(<ReviewPage request={request} />);

    // whoami gives a verified identity, so the filter is enabled from the start.
    await waitFor(() => expect(screen.getByText("churn_mine")).toBeTruthy());
    expect(screen.getByText("churn_theirs")).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText("Assigned to me").disabled).toBe(false));

    // Claim the only unassigned task; the server assigns it to THIS caller.
    fireEvent.click(screen.getByText("Assign to me"));
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/v0/set_review_assignee", { task_id: "mine", assigned: true });
    });
    fireEvent.click(screen.getByLabelText("Assigned to me"));
    await waitFor(() => expect(screen.getByText("churn_mine")).toBeTruthy());
    expect(screen.queryByText("churn_theirs")).toBeNull();
    expect(screen.getByText("Assigned to you")).toBeTruthy();
  });

  it("an anonymous caller (null identity) DISABLES the filter -- anonymous-owned tasks are not 'mine'", async () => {
    // authz off: every self-claim stores the shared 'anonymous' id. whoami returns null,
    // so the filter is disabled and no anonymous-owned task reads as 'assigned to you'.
    const { request } = makeRequest([task("a", "anonymous", "churn_a")], null);
    render(<ReviewPage request={request} />);
    await waitFor(() => expect(screen.getByText("churn_a")).toBeTruthy());
    expect(screen.getByLabelText("Assigned to me").disabled).toBe(true);
    expect(screen.queryByText("Assigned to you")).toBeNull();
    expect(screen.getByText("Owned by anonymous")).toBeTruthy();
  });

  it("'me' follows the SERVER's whoami, not any client value -- a re-login as a different user is not stale", async () => {
    // The authority is whoami (here OTHER), so a task the SERVER owns to OTHER reads as
    // 'assigned to you' and a task owned by ME does not -- no persisted/forged client id
    // can make a task 'mine'.
    const { request } = makeRequest([task("theirs", OTHER, "churn_theirs"), task("mine", ME, "churn_mine")], OTHER);
    render(<ReviewPage request={request} />);
    await waitFor(() => expect(screen.getByText("churn_theirs")).toBeTruthy());
    // whoami resolves async; the OTHER-owned task becomes 'yours' once it lands.
    await waitFor(() => expect(screen.getByText("Assigned to you")).toBeTruthy());  // OTHER-owned == me
    expect(screen.getByText(`Owned by ${ME}`)).toBeTruthy();       // ME-owned != me
  });
});
