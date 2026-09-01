"""The raw-metadata arm's operation NAMES, as a leaf of shared vocabulary.

Just the two operation-name strings, with no dependency of their own. They live
here rather than in `raw_arm.py` because two very different modules need them:
`raw_arm.py` builds the tool specs and the executor (which import
`repositories.postgres` and `planner.executor`), while `source_identity.py` only
needs to RECOGNISE `get_raw_asset` in a recorded trace when deciding which tool
results carry a source version. Importing the whole arm for one string dragged
`repositories.postgres` into the reporting/scoring import closure (hy-quol); a
leaf module of names lets the recogniser import the vocabulary without the
runtime. `raw_arm.py` imports these back, so the specs still key off the same
constants.
"""

from __future__ import annotations

LIST_RAW_ASSETS = "list_raw_assets"
GET_RAW_ASSET = "get_raw_asset"
