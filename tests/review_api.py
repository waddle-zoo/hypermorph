"""Served review-op HTTP paths, derived from the operation names (hy-es7z).

The `/review` UI and its tests reach the review operations through the SERVED
operation paths (`POST /v0/<operation>`, auto-registered in
`hyperset.transport.http.ROUTES`), not the removed bespoke `/v0/review/*`
adapters. Deriving the path from the operation-name constant here means a rename
in `hyperset.transport.operations` propagates to every call site instead of
drifting per test -- the hardcoded-per-site pattern is exactly what left the
adapters and their tests scattered.
"""

from __future__ import annotations

from hyperset.transport.operations import (
    EDIT_REVIEW_DRAFT,
    GET_REVIEW_TASK,
    LIST_REVIEW_TASKS,
    PROPOSE_REVIEW_TO_GIT,
    REFINE_REVIEW_DRAFT,
    SET_REVIEW_ASSIGNEE,
)


def review_op_path(operation: str) -> str:
    """The served path for a review operation: `/v0/<operation>`.

    Mirrors `ROUTES = {f"/v0/{name}": name for name in OPERATIONS}` so the path is
    the operation name and nothing bespoke.
    """
    return f"/v0/{operation}"


LIST_REVIEW_TASKS_PATH = review_op_path(LIST_REVIEW_TASKS)
GET_REVIEW_TASK_PATH = review_op_path(GET_REVIEW_TASK)
EDIT_REVIEW_DRAFT_PATH = review_op_path(EDIT_REVIEW_DRAFT)
REFINE_REVIEW_DRAFT_PATH = review_op_path(REFINE_REVIEW_DRAFT)
PROPOSE_REVIEW_TO_GIT_PATH = review_op_path(PROPOSE_REVIEW_TO_GIT)
SET_REVIEW_ASSIGNEE_PATH = review_op_path(SET_REVIEW_ASSIGNEE)
