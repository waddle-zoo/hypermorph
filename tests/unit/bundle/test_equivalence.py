"""What counts as the same computation (hy-gh-128).

The comparator answers three ways, and the tests are grouped by the answer:
folded spellings, relaxed-but-undecidable differences, and real differences.
Every case here is a fragment a warehouse or a manifest actually produces.
"""

from __future__ import annotations

import pytest

from hyperset.bundle.equivalence import DIFFERENT, EQUIVALENT, UNDECIDED, compare_fragments


@pytest.mark.parametrize(
    ("governed", "proposed"),
    [
        # Whitespace and punctuation spacing.
        ("SUM(amount)", "sum( amount )"),
        ("region = 'CA'", "region='CA'"),
        ("order_date\n  by region", "order_date by region"),
        # Case of keywords and unquoted identifiers.
        ("SUM(Gross_Amount - tax_amount)", "sum(gross_amount - TAX_AMOUNT)"),
        # An output alias names the result; it does not compute it, and the
        # governed field's own `name` is compared separately.
        ("SUM(amount)", "SUM(amount) AS recognized_revenue"),
        # Parentheses around the whole fragment.
        ("SUM(amount) - SUM(refund)", "(SUM(amount) - SUM(refund))"),
        # A one-element IN list selects what the equality selects.
        ("region = 'CA'", "region IN ('CA')"),
    ],
)
def test_a_spelling_that_cannot_change_a_value_is_the_same_computation(governed, proposed):
    assert compare_fragments(governed, proposed) == EQUIVALENT


@pytest.mark.parametrize(
    ("governed", "proposed"),
    [
        ("SUM(payload['amt'])", "SUM(payload[ 'amt' ])"),
        ("ANY(ARRAY[1, 2])", "ANY(ARRAY[1,2])"),
        ("SUM(arr[IDX])", "SUM(arr[idx])"),
    ],
)
def test_a_reformatted_subscript_is_the_same_computation(governed, proposed):
    """`[` is T-SQL's identifier quote and Postgres's array/JSON subscript,
    and Postgres is the dialect this repo ships against. Lexing the bracket
    verbatim kept the whitespace and the case of an ORDINARY EXPRESSION, so a
    reformatted governed subscript turned every plan built on it `invalid` --
    the exact failure hy-gh-128 exists to remove, reintroduced by the fix for
    hy-fae7 (hy-eyqa).

    Not the mirror image of `` ` `` and `$$`, and they stay lexed verbatim: a
    backtick is not valid Postgres at all, so holding its contents can only
    affect input that was never valid here, while `[` IS valid Postgres with a
    different meaning. Folding a T-SQL `[Region]` is correct anyway -- bracket
    identifiers are case-insensitive under the default collation.

    The premise that argument rests on -- Postgres is the dialect that decides
    -- used to be written down HERE and in no place a future editor would be
    standing. It is now stated at `_QUOTES` in `hyperset/bundle/equivalence.py`
    with the trigger that reopens it, and in `docs/v0-foundation.md` section 7,
    which is the binding text (hy-f37x).
    """
    assert compare_fragments(governed, proposed) == EQUIVALENT


@pytest.mark.parametrize(
    ("governed", "proposed", "verdict"),
    [
        # The consequence section 7 now states outright, and the one a plan
        # author can observe: a T-SQL identifier quote is not a delimiter here.
        ("[Region] = 1", "[region] = 1", EQUIVALENT),
        # Every delimiter that IS one, in the same position, for contrast.
        ('"Region" = 1', '"region" = 1', DIFFERENT),
        ("`Region` = 1", "`region` = 1", DIFFERENT),
        ("tag = 'Region'", "tag = 'region'", DIFFERENT),
        ("tag = $$Region$$", "tag = $$region$$", DIFFERENT),
    ],
)
def test_case_survives_every_delimiter_the_lexer_holds_and_not_the_bracket(
    governed, proposed, verdict
):
    """The asymmetry stated as behaviour rather than as a comment, because a
    comment is what it was and section 7 contradicted it (hy-f37x).

    `docs/v0-foundation.md` section 7 names these four and excludes `[`;
    `tests/unit/test_section_7_matches_the_served_contract.py` holds that
    sentence to `_QUOTES`. This row set is the other half: what the named
    delimiters DO to a case difference at the point a plan is validated.
    """
    assert compare_fragments(governed, proposed) == verdict


@pytest.mark.parametrize(
    ("governed", "proposed"),
    [
        # The unqualified column may be this one or another source's.
        ("SUM(amount)", "SUM(orders.amount)"),
        ("SUM(orders.amount)", "SUM(o.amount)"),
        ("order_date by customer_dim.region", "order_date by region"),
        # A cast is a no-op on a date and a truncation on a timestamp.
        ("posting_date", "posting_date::date"),
        ("amount", "amount::numeric(10,2)"),
    ],
)
def test_a_difference_only_the_warehouse_can_settle_is_undecided(governed, proposed):
    assert compare_fragments(governed, proposed) == UNDECIDED


@pytest.mark.parametrize(
    ("governed", "proposed"),
    [
        # A term the other side does not have.
        ("SUM(gross_amount - tax_amount)", "SUM(gross_amount)"),
        # Same names, different number: operand order is never relaxed.
        ("gross_amount - tax_amount", "tax_amount - gross_amount"),
        ("SUM(amount)", "AVG(amount)"),
        ("status = 'completed'", "status <> 'completed'"),
        # Case inside a literal is data, not spelling: different rows.
        ("status = 'completed'", "status = 'Completed'"),
        # A quoted identifier is case-sensitive by the same argument.
        ('"region"', '"Region"'),
        ("region IN ('CA', 'NY')", "region = 'CA'"),
        ("order_date by region", "order_date"),
    ],
)
def test_a_different_computation_stays_different(governed, proposed):
    assert compare_fragments(governed, proposed) == DIFFERENT


@pytest.mark.parametrize(
    ("governed", "proposed", "delimiter"),
    [
        # Postgres dollar-quoting, and the case that matters most: it is the
        # form a generated filter reaches for when the value contains a quote,
        # and what is inside it is data exactly as `'...'` is.
        ("status = $$Completed$$", "status = $$completed$$", "$$"),
        ("status = $tag$Completed$tag$", "status = $tag$completed$tag$", "$tag$"),
        # MySQL's spelling of a quoted identifier. T-SQL's `[Region]` is not
        # here: `[` is a Postgres subscript, so lexing it as a delimiter cost
        # more than it bought (hy-eyqa).
        ("`Region` = 1", "`region` = 1", "`"),
        # The two the first version of the lexer already got right, kept here
        # so a fix for the other three cannot quietly break them.
        ("status = 'Completed'", "status = 'completed'", "'"),
        ('status = "Completed"', 'status = "completed"', '"'),
    ],
)
def test_case_inside_every_quoting_form_is_data(governed, proposed, delimiter):
    """The lexer knew two delimiters and case-folded the rest, so
    `$$Completed$$` and `$$completed$$` compared equal and a plan filtering on
    the wrong rows validated with no violations at all (hy-fae7).

    That contradicted three statements this branch shipped: the module's own
    "the case and content of string literals and quoted identifiers are never
    folded", section 7's "`'Completed'` and `'completed'` are different rows",
    and the issue's "unsure -> disclose, do not silently pass". A guard is
    worth what it has failed at, so all five pairs are pinned here and not only
    the three that were wrong.
    """
    assert compare_fragments(governed, proposed) == DIFFERENT, delimiter


def test_a_missing_fragment_is_not_quietly_equal_to_a_stated_one():
    assert compare_fragments("order_date by region", "") == DIFFERENT
    assert compare_fragments("", "") == EQUIVALENT


def test_the_comparison_is_symmetric_and_repeatable():
    pair = ("SUM(orders.amount)", "sum( amount )::numeric")
    assert compare_fragments(*pair) == compare_fragments(*reversed(pair)) == UNDECIDED
    assert compare_fragments(*pair) == compare_fragments(*pair)


def test_an_unterminated_quote_is_compared_rather_than_crashing():
    assert compare_fragments("status = 'completed", "status = 'completed") == EQUIVALENT
    assert compare_fragments("status = 'completed", "status = 'Completed") == DIFFERENT
