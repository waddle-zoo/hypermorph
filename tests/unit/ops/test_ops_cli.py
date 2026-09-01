"""The `hyperset ops status` subcommand parses and dispatches (hy-9vji)."""

from __future__ import annotations

import hyperset.cli


def test_ops_status_parses_and_dispatches():
    parser = hyperset.cli.build_parser()
    args = parser.parse_args(["ops", "status"])
    assert args.func.__name__ == "cmd_ops_status"


def test_ops_requires_a_subcommand():
    import pytest

    parser = hyperset.cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ops"])
