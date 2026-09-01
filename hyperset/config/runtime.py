"""The active, loaded deployment settings for the running process (hy-tc4o, config slice 3).

Config slice 1 built the loader and slice 2 the secret resolution; both were INERT. This
holds the ONE validated settings object the serving process loaded at startup, so a call site
reads a value from the layered config instead of a scattered `os.environ.get`. It is set ONCE
by `apply_startup_config` (the `serve` entrypoints) and not mutated after. A process that
never ran startup -- a unit test, a CLI subcommand that does not serve -- leaves it unset, and
each domain accessor falls back to the exact legacy env read, so migrating a domain through
here is behaviour-preserving.
"""

from __future__ import annotations

_ACTIVE: dict | None = None


def set_active_settings(settings: dict) -> None:
    """Record the settings the serving process loaded at startup."""
    global _ACTIVE
    _ACTIVE = settings


def active_settings() -> dict | None:
    """The settings the serving process loaded, or None if startup has not run."""
    return _ACTIVE


def clear_active_settings() -> None:
    """Reset to the not-loaded state. Tests only, so one test's startup never leaks."""
    global _ACTIVE
    _ACTIVE = None
