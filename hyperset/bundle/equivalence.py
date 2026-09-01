"""Semantic equivalence for the SQL fragments a plan and a bundle both state
(hy-gh-128).

The validator used to compare expressions, filters, and grain by
whitespace-collapsed string equality. On a real warehouse that is a trap: the
day someone reformats the governed expression, every plan built on it turns
`invalid` and the manifest has to be hand-resynced to the exact characters.
The governed meaning had not changed; only its spelling had.

So comparison happens on tokens, and the answer has three values instead of
two:

- `EQUIVALENT` -- the two fragments are the same computation. Only spellings
  that cannot change a value are folded: whitespace, punctuation spacing, the
  case of keywords and unquoted identifiers, redundant outer parentheses, a
  trailing output alias, and `x IN (one literal)` written as `x = literal`.
- `UNDECIDED` -- the difference is confined to table qualifiers (`SUM(o.amount)`
  against `SUM(amount)`) or casts (`posting_date` against
  `posting_date::date`). Either may be the same value or may not, and deciding
  it needs the warehouse schema or the query itself. Hyperset has neither: it
  does not execute SQL (`docs/v0-foundation.md` invariant 6). The caller is
  told, with both forms, rather than being told it is wrong.
- `DIFFERENT` -- everything else, including reordered operands. `a - b` and
  `b - a` use the same names and are not the same number, so token order is
  never relaxed away.

What is deliberately NOT folded: the case and content of string literals and
quoted identifiers, in every delimiter `_QUOTES` and `_DOLLAR_QUOTE` name.
`'Completed'` and `'completed'` are different rows, and so are `$$Completed$$`
and `$$completed$$`.

Conservative by construction: a fold is only ever applied to BOTH fragments,
so the relaxations can merge two spellings of one computation but can never
make two genuinely different computations compare equal -- the worst they do
is downgrade a difference to a disclosure, which is the direction the
governance rule allows.
"""

from __future__ import annotations

import re

EQUIVALENT = "equivalent"
UNDECIDED = "undecided"
DIFFERENT = "different"

# Long enough to matter, short enough that an unknown pair splits into single
# characters on both sides and compares identically anyway.
_OPERATOR_PAIRS = frozenset({"::", "<=", ">=", "<>", "!=", "||"})

# Every delimiter that makes the text inside it data. A delimiter the lexer
# does not know is worse than one it handles clumsily: the text inside gets
# case-folded with everything else, and two literals that select different
# rows compare equal (hy-fae7). `'` and `"` are the standard pair, `` ` `` is
# MySQL's, and `$$...$$` / `$tag$...$tag$` is Postgres dollar-quoting -- the
# form a generated filter reaches for exactly when the value contains a quote,
# which is when getting it wrong costs most.
#
# T-SQL's `[...]` is deliberately absent, and the asymmetry is the point. The
# PREMISE first, because the consequence is unreadable without it and it was
# previously written down only in a test docstring: POSTGRES IS THE ONLY
# WAREHOUSE DIALECT HYPERSET SHIPS AGAINST, so Postgres is what decides whether
# a character is a delimiter here. On that premise a backtick is not valid
# Postgres at all, so holding its contents verbatim can only affect input that
# was never valid here, while `[` IS valid Postgres -- an array/JSON subscript
# whose contents are an ordinary expression. Lexing it as a delimiter made a
# reformatted subscript a contradiction (hy-eyqa), and nothing is lost against
# the dialect shipped: T-SQL bracket identifiers are case-insensitive under the
# default collation, so folding them is right rather than a false pass.
#
# THE TRIGGER THAT REOPENS THIS, named so it is not rediscovered as a bug: a
# second warehouse dialect. Under a case-sensitive T-SQL collation `[Region]`
# and `[region]` are different identifiers and folding them becomes a real
# false pass -- the comparator would call two different computations the same,
# which is the one direction ADR 0021 does not allow. `docs/v0-foundation.md`
# section 7 states the same trigger, because that is where a reader looking for
# the contract will be standing (hy-f37x).
_QUOTES = "'\"`"
_DOLLAR_QUOTE = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$")


def compare_fragments(governed: str, proposed: str) -> str:
    """Compare two SQL fragments. Deterministic and pure."""
    left = _canonical(governed)
    right = _canonical(proposed)
    if left == right:
        return EQUIVALENT
    if _relaxed(left) == _relaxed(right):
        return UNDECIDED
    return DIFFERENT


def canonical_key(fragment: str) -> tuple[str, ...]:
    """The comparison key, exposed so a caller can de-duplicate a list of
    fragments the same way `compare_fragments` would judge them."""
    return _canonical(fragment)


def _canonical(fragment: str) -> tuple[str, ...]:
    tokens = _tokens(fragment)
    tokens = _without_output_alias(tokens)
    tokens = _without_outer_parens(tokens)
    return tuple(_single_value_in_as_equals(tokens))


def _relaxed(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_without_casts(_without_qualifiers(list(tokens))))


def _tokens(fragment: str) -> list[str]:
    """Lex far enough to tell data from spelling, and no further.

    A parser would be the honest tool, and it would be a dependency plus a
    dialect GRAMMAR for a comparison that only has to answer "same or not".

    Not "plus a dialect decision", which is what this said and which the
    `_QUOTES` comment above falsifies eight lines up: the lexer makes exactly
    one dialect decision, that Postgres decides what is a delimiter, and it is
    stated there with its trigger. The distinction is the whole reason the
    trade is worth taking -- one premise a reader can check against one comment
    is not the same cost as a grammar per dialect (hy-f37x).
    """
    source = str(fragment)
    tokens: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
        elif char in _QUOTES:
            end = _closing_quote(source, index)
            # Verbatim: what is inside the quotes is a value, not a spelling.
            tokens.append(source[index:end])
            index = end
        elif char == "$" and _DOLLAR_QUOTE.match(source, index):
            end = _closing_dollar_quote(source, index)
            tokens.append(source[index:end])
            index = end
        elif char.isalnum() or char == "_":
            end = index
            while end < length and (source[end].isalnum() or source[end] in "_$"):
                end += 1
            tokens.append(source[index:end].lower())
            index = end
        elif source[index : index + 2] in _OPERATOR_PAIRS:
            tokens.append(source[index : index + 2])
            index += 2
        else:
            tokens.append(char)
            index += 1
    return tokens


def _closing_quote(source: str, start: int) -> int:
    """One character past the quote that ends the run opened at `start`."""
    quote = source[start]
    index = start + 1
    while index < len(source):
        if source[index] != quote:
            index += 1
        elif source[index : index + 2] == quote * 2:
            index += 2  # doubled delimiter is an escaped one, not the end
        else:
            return index + 1
    return len(source)  # unterminated: the rest is one token, on both sides


def _closing_dollar_quote(source: str, start: int) -> int:
    tag = _DOLLAR_QUOTE.match(source, start).group()
    end = source.find(tag, start + len(tag))
    return len(source) if end < 0 else end + len(tag)


def _without_output_alias(tokens: list[str]) -> list[str]:
    """`SUM(amount) AS revenue` computes what `SUM(amount)` computes.

    The name a field is served under is the governed field's `name`, which the
    validator compares on its own, so an alias here is decoration.
    """
    if len(tokens) >= 3 and tokens[-2] == "as" and _is_name(tokens[-1]):
        head = tokens[:-2]
        if _is_balanced(head):
            return head
    return tokens


def _without_outer_parens(tokens: list[str]) -> list[str]:
    while len(tokens) >= 2 and tokens[0] == "(" and _closing_paren(tokens, 0) == len(tokens) - 1:
        tokens = tokens[1:-1]
    return tokens


def _single_value_in_as_equals(tokens: list[str]) -> list[str]:
    """`region IN ('CA')` and `region = 'CA'` select the same rows, NULL
    included. One-element lists are what a generated filter tends to produce
    and what a hand-written manifest tends not to."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        if (
            tokens[index] == "in"
            and tokens[index + 1 : index + 2] == ["("]
            and tokens[index + 3 : index + 4] == [")"]
        ):
            out.extend(["=", tokens[index + 2]])
            index += 4
        else:
            out.append(tokens[index])
            index += 1
    return out


def _without_qualifiers(tokens: list[str]) -> list[str]:
    """`orders.amount` and `amount` may be the same column or may not: with
    two joined sources in the plan, the unqualified name is ambiguous and only
    the warehouse can say. Undecidable, so relaxed rather than folded."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        if (
            _is_name(tokens[index])
            and tokens[index + 1 : index + 2] == ["."]
            and index + 2 < len(tokens)
            and _is_name(tokens[index + 2])
        ):
            index += 2  # drop the qualifier and its dot; chains reduce to the last part
        else:
            out.append(tokens[index])
            index += 1
    return out


def _without_casts(tokens: list[str]) -> list[str]:
    """A cast may be a no-op or may truncate. `posting_date::date` on a
    timestamp is not `posting_date`; on a date it is."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] == "::":
            index += 2  # the operator and the type name
            if tokens[index : index + 1] == ["("]:  # a length or precision
                index = _closing_paren(tokens, index) + 1
        else:
            out.append(tokens[index])
            index += 1
    return out


def _closing_paren(tokens: list[str], start: int) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == "(":
            depth += 1
        elif tokens[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _is_balanced(tokens: list[str]) -> bool:
    depth = 0
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_name(token: str) -> bool:
    return bool(token) and (token[0].isalpha() or token[0] == "_")
