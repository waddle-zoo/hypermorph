"""The new-mypy-errors diff gate's pure logic (hy-djml step 2).

The subprocess/worktree half is exercised by running the script; these pin the
parse and multiset-diff that decide what counts as NEW, including the two honest
costs the script documents: line numbers are stripped (so an error is matched by
identity, not position) and the comparison is a MULTISET (so a second copy of an
existing error is caught, and a pure file move reads as new).
"""

from __future__ import annotations

from collections import Counter

import pytest

from scripts.mypy_new_errors import (
    MypyInvocationError,
    checked_errors,
    new_errors,
    parse_errors,
)


def test_parse_strips_the_line_number_from_the_key():
    # Same file/message/code at two different lines collapse to one key, count 2.
    output = (
        'hyperset/a.py:10: error: Argument 1 has incompatible type "int"  [arg-type]\n'
        'hyperset/a.py:99: error: Argument 1 has incompatible type "int"  [arg-type]\n'
    )
    parsed = parse_errors(output)
    assert parsed == Counter(
        {("hyperset/a.py", "arg-type", 'Argument 1 has incompatible type "int"'): 2}
    )


def test_parse_ignores_notes_and_summary_lines():
    output = (
        "hyperset/a.py:3: error: Incompatible return value type  [return-value]\n"
        "hyperset/a.py:3: note: Consider using a type alias\n"
        "Found 1 error in 1 file (checked 2 source files)\n"
    )
    parsed = parse_errors(output)
    assert parsed == Counter(
        {("hyperset/a.py", "return-value", "Incompatible return value type"): 1}
    )


def test_parse_handles_a_column_and_a_missing_code():
    output = (
        "hyperset/a.py:5:12: error: Name 'x' is not defined  [name-defined]\n"
        "hyperset/b.py:1: error: Cannot find implementation\n"
    )
    parsed = parse_errors(output)
    assert parsed[("hyperset/a.py", "name-defined", "Name 'x' is not defined")] == 1
    assert parsed[("hyperset/b.py", "", "Cannot find implementation")] == 1


def test_new_errors_is_empty_when_head_matches_base():
    base = parse_errors("hyperset/a.py:10: error: boom  [misc]\n")
    head = parse_errors("hyperset/a.py:42: error: boom  [misc]\n")  # moved line, same key
    assert new_errors(base, head) == Counter()


def test_a_genuinely_new_error_is_reported():
    base = parse_errors("hyperset/a.py:1: error: old  [misc]\n")
    head = parse_errors(
        "hyperset/a.py:1: error: old  [misc]\nhyperset/a.py:2: error: brand new  [arg-type]\n"
    )
    assert new_errors(base, head) == Counter({("hyperset/a.py", "arg-type", "brand new"): 1})


def test_a_second_copy_of_an_existing_error_counts_as_one_new():
    # The multiset cost: without it, adding a duplicate of a base error would slip.
    base = parse_errors("hyperset/a.py:1: error: dup  [misc]\n")
    head = parse_errors(
        "hyperset/a.py:1: error: dup  [misc]\nhyperset/a.py:9: error: dup  [misc]\n"
    )
    assert new_errors(base, head) == Counter({("hyperset/a.py", "misc", "dup"): 1})


def test_a_pure_file_move_reads_as_new_the_documented_false_positive():
    # Moving already-failing code to another file changes the file component of the
    # key, so it reads as new. Documented, asserted so the behaviour is pinned.
    base = parse_errors("hyperset/old.py:3: error: boom  [misc]\n")
    head = parse_errors("hyperset/new.py:3: error: boom  [misc]\n")
    assert new_errors(base, head) == Counter({("hyperset/new.py", "misc", "boom"): 1})


def test_fixing_an_error_is_not_new():
    base = parse_errors("hyperset/a.py:1: error: a  [misc]\nhyperset/a.py:2: error: b  [misc]\n")
    head = parse_errors("hyperset/a.py:1: error: a  [misc]\n")
    assert new_errors(base, head) == Counter()


# --- Fail closed: a mypy run that did not complete as a type-error check must NOT
# --- be read as zero errors (adversary bounce on the discarded return code).


def test_a_clean_run_returncode_zero_parses():
    assert checked_errors("Success: no issues found in 5 source files\n", 0) == Counter()


def test_a_type_error_run_returncode_one_parses():
    output = "hyperset/a.py:1: error: boom  [misc]\nFound 1 error in 1 file\n"
    assert checked_errors(output, 1) == Counter({("hyperset/a.py", "misc", "boom"): 1})


def test_empty_output_with_success_is_a_real_zero():
    # A genuinely clean run (returncode 0) with no error lines is zero, not a failure.
    assert checked_errors("", 0) == Counter()


@pytest.mark.parametrize("returncode", [2, 127, -6, 3])
def test_a_non_type_error_returncode_fails_closed(returncode):
    # 2 = fatal/usage, 127 = uvx could not run mypy, negative = killed by a signal.
    # Each yields empty/partial output that would parse as zero errors and silently
    # disable the gate; it must raise instead.
    with pytest.raises(MypyInvocationError):
        checked_errors("", returncode)


def test_a_fatal_run_that_still_printed_error_lines_fails_closed():
    # Even if a fatal run emits something error-shaped, a bad return code means the
    # measurement is untrustworthy -- refuse it rather than parse it.
    output = "hyperset/a.py:1: error: real  [misc]\nmypy: internal error\n"
    with pytest.raises(MypyInvocationError):
        checked_errors(output, 2)


def test_the_fail_closed_error_carries_the_output_for_diagnosis():
    with pytest.raises(MypyInvocationError) as excinfo:
        checked_errors("error: unrecognized arguments: --nope", 2)
    assert "unrecognized arguments" in str(excinfo.value)
    assert "exited 2" in str(excinfo.value)
