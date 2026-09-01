"""`git_pr.read_pr_state` FAILS CLOSED on a malformed 2xx GitHub payload (hq-ci92
#436 round 2). No network: `requests.get` is monkeypatched. A merge is asserted
only from an explicit merged signal; an unmerged PR must carry a recognised
state; anything unclassifiable is 'unknown' with merged False -- never a guessed
merge and never a silent 'open'.
"""

from __future__ import annotations

import pytest

from hyperset.flywheel import git_pr

REPO = "https://github.com/acme/context"
HEAD = "hyperset/proposal/revenue-abc"


class _Resp:
    def __init__(self, payload, *, status_code=200, raises=False):
        self._payload = payload
        self.status_code = status_code
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


def _get_returning(resp):
    def _get(*_args, **_kwargs):
        return resp

    return _get


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(None, raises=True),  # body is not JSON
        _Resp({"not": "a list"}),  # not a list
        _Resp([]),  # empty list
        _Resp(["not-a-mapping"]),  # first item not a mapping
        _Resp([{"number": 1}]),  # unmerged with NO state -> not a silent 'open'
        _Resp([{"number": 1, "state": "weird"}]),  # unrecognised state
        _Resp([{"number": 1}], status_code=500),  # non-2xx
    ],
)
def test_a_malformed_or_unclassifiable_payload_is_unknown_not_a_guess(resp, monkeypatch):
    monkeypatch.setattr(git_pr.requests, "get", _get_returning(resp))
    out = git_pr.read_pr_state(repository=REPO, head_branch=HEAD)
    assert out == {"state": "unknown", "pr_url": None, "pr_number": None, "merged": False}


def test_a_non_github_target_is_unknown_without_a_request(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("no request for a non-github target")

    monkeypatch.setattr(git_pr.requests, "get", _boom)
    out = git_pr.read_pr_state(repository="/srv/local-repo", head_branch=HEAD)
    assert out["state"] == "unknown" and out["merged"] is False


def test_a_merged_pr_is_read_as_merged(monkeypatch):
    resp = _Resp(
        [
            {
                "number": 7,
                "state": "closed",
                "merged_at": "2026-08-21T00:00:00Z",
                "html_url": "https://github.com/acme/context/pull/7",
            }
        ]
    )
    monkeypatch.setattr(git_pr.requests, "get", _get_returning(resp))
    out = git_pr.read_pr_state(repository=REPO, head_branch=HEAD)
    assert out == {
        "state": "merged",
        "pr_url": "https://github.com/acme/context/pull/7",
        "pr_number": 7,
        "merged": True,
    }


def test_a_closed_unmerged_pr_is_read_as_closed_unmerged(monkeypatch):
    resp = _Resp([{"number": 8, "state": "closed", "merged_at": None}])
    monkeypatch.setattr(git_pr.requests, "get", _get_returning(resp))
    out = git_pr.read_pr_state(repository=REPO, head_branch=HEAD)
    assert out["state"] == "closed_unmerged" and out["merged"] is False


def test_an_open_pr_is_read_as_open(monkeypatch):
    resp = _Resp([{"number": 9, "state": "open", "merged_at": None}])
    monkeypatch.setattr(git_pr.requests, "get", _get_returning(resp))
    out = git_pr.read_pr_state(repository=REPO, head_branch=HEAD)
    assert out["state"] == "open" and out["merged"] is False
