"""A clean checkout must boot (GitHub #320, hy-7a5x).

The runtime image bakes its virtualenv at build time as root, then drops to the
unprivileged `hyperset` user. `uv run` re-resolves the project into `.venv` on
every start unless told not to, and that write fails against the root-owned venv
on a clean checkout's first `make up`. `UV_NO_SYNC=1` makes `uv run` use the baked
environment (into which `uv sync --frozen` already installed the `hyperset` console
script) as-is.

This guard keeps the ENV present AND ahead of the ENTRYPOINT, so a later Dockerfile
cleanup that reads `uv run hyperset` beside a redundant-looking env var cannot
silently reintroduce the first-boot failure. The person who would delete it is
reading the Dockerfile, not this module, which is why the reason lives there and the
check lives here (the pattern `test_the_default_bind_is_loopback` uses). A full
build-and-boot of the image is the manual `make up` check and the compose suite;
this is the fast gate that pins the one line the fix is."""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "hyperset.Dockerfile"


def _lines() -> list[str]:
    return DOCKERFILE.read_text().splitlines()


def test_the_runtime_image_skips_the_start_time_uv_sync():
    lines = _lines()
    env_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "ENV UV_NO_SYNC=1"), None
    )
    entrypoint_index = next(
        (i for i, line in enumerate(lines) if line.startswith("ENTRYPOINT")), None
    )

    assert env_index is not None, (
        "docker/hyperset.Dockerfile must set `ENV UV_NO_SYNC=1`, or `uv run` re-resolves "
        "the project into a root-owned .venv at start and a clean-checkout `make up` fails "
        "(GitHub #320)"
    )
    assert entrypoint_index is not None, "docker/hyperset.Dockerfile must declare an ENTRYPOINT"
    assert env_index < entrypoint_index, (
        "`ENV UV_NO_SYNC=1` must precede the ENTRYPOINT so it is in effect when "
        "`uv run hyperset` starts the container"
    )
