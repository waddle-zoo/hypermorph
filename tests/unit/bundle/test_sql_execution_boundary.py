"""The SQL execution boundary, enforced (ADR-0032, hy-iz3o).

Hyperset core never runs, generates, or validates the customer's SQL -- a permanent
platform boundary. The `execution` disclosure is therefore permanently false, and the
one core "validation" surface (`validate_analytics_plan`) checks a PLAN against
governance without touching a database. These pin the ADR's claims in code, so a
future change that tried to flip an execution field or run a query in core fails here.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from hyperset.bundle.schema import ContextBundle

CORE = Path(__file__).resolve().parents[2].parent / "hyperset"
_EXECUTION_KEYS = ("performed_by_hyperset", "result_validated_by_hyperset")


def test_the_execution_disclosure_defaults_to_false():
    field = next(f for f in dataclasses.fields(ContextBundle) if f.name == "execution")
    assert field.default_factory() == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


def test_no_core_module_ever_assigns_an_execution_field_a_truthy_value():
    # Every ASSIGNMENT (`key: <v>` or `key = <v>`) to an execution key across the core
    # tree must be False -- there is no truthy writer, so the disclosure cannot flip.
    offenders: list[str] = []
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for key in _EXECUTION_KEYS:
            for match in re.finditer(rf"['\"]?{key}['\"]?\s*[:=]\s*(\w+)", text):
                if match.group(1) != "False":
                    offenders.append(f"{path.relative_to(CORE.parent)}: {key} = {match.group(1)}")
    assert offenders == [], offenders


def test_the_plan_validator_and_equivalence_run_no_sql():
    # The in-core "validation" surface reads no database and runs no query: no db
    # driver import, no cursor, no `.execute(` call. It compares a plan against the
    # governed bundle deterministically, nothing more.
    for name in ("plan.py", "equivalence.py"):
        source = (CORE / "bundle" / name).read_text(encoding="utf-8")
        # Strip comments and string literals so PROSE like "does not execute the query"
        # is not read as an execution primitive; only real code is checked.
        code = re.sub(r"#.*", "", source)
        code = re.sub(r"(?s)(['\"]{3}).*?\1", "", code)
        code = re.sub(r"(['\"]).*?\1", "", code)
        # A driver import, a connection, a cursor, or any `.execute`/`.executemany`
        # call is a query primitive; none may appear in the in-core validator.
        for driver in ("psycopg", "sqlalchemy", "sqlite3", "duckdb"):
            assert driver not in code, (name, driver)
        assert ".connect(" not in code, name
        assert "cursor" not in code, name
        assert not re.search(r"\.execute(many)?\s*\(", code), name
