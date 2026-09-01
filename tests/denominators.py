"""Test double for the half of hy-6nit that is not built yet.

`run_sync` soft-deletes only the asset types whose snapshot carries an
`EstablishedDenominator`, and no shipped connector produces one: DataHub's
`total` is capped at 10000 with the cap invisible to the client, and
Superset's listing measures completeness in rows, not identities (hy-fc01).
The producer half needs `datahub/graphql.py` and waits on #180.

So without this mixin every test in the suite asserts "nothing was deleted",
and the suite would pass unchanged against a gate hard-coded to refuse. Mixed
into a real connector, it lets a test exercise the branch that PERMITS a
deletion -- and it is deliberately the only way to reach that branch, so a
grep for this class finds every deletion the suite still expects.
"""

from __future__ import annotations

from dataclasses import replace

from hyperset.connectors import EstablishedDenominator

PRODUCER = "test double standing in for a real completeness instrument"


class EstablishesDenominators:
    """Mix in BEFORE a connector class; establishes a denominator for each
    asset type named in `_warranted` and for no other."""

    _warranted: tuple[str, ...] = ()

    def snapshot(self, checkpoint=None):
        return replace(
            super().snapshot(checkpoint),
            established_denominators={
                asset_type: EstablishedDenominator(producer=PRODUCER)
                for asset_type in self._warranted
            },
        )
