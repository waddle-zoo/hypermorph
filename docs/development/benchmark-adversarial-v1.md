# Simulated-expert adversarial benchmark — completion criteria and spec (V1, #141)

Status: SPEC (2026-08-15, hy-qz8p). This is the committed completion-criteria document
for GitHub #141. It makes the benchmark's success condition concrete and testable so
the gate can be BUILT and MEASURED; it is not the implementation. Every threshold below
is a named number, not a TBD.

This benchmark is the exploration/regression sibling of the `#25` deterministic release
gate (`docs/development/benchmark.md`). It reuses the existing eval scaffolding in
`hyperset/evals/` and the served MCP contract; it introduces **no new agent or eval
framework**. In particular it uses `inspect_ai` as the harness, as `#25` does
(`hyperset/evals/task.py`), and adds **no DeepEval layer** — that is explicitly
forbidden (`docs/directives/mayor.md`: "There is no DeepEval layer — do not add one").

## 1. Scope boundary (HARD INVARIANT)

This benchmark is an **exploration and regression-detection layer. It is NOT part of
`#25`'s required release gate, and it MUST NOT block a merge.** It may fail loudly and
inform product decisions without failing any build. This is the same relationship
`#96`'s agent-driven eval has to `#25`, and it is the posture ADR 0013 already fixed for
the model-dependent arms: "A scheduled failure does not block a merge by itself. Acting
on one is a human decision" (`docs/adr/0013-split-benchmark-gate.md`). ADR 0007's ruling
stands unchanged: the **deterministic** graders are the required gate; a model judge is
optional/exploration only. Concretely:

- `#25` stays human-authored, deterministic, locked-question, no-LLM-judge, unchanged.
  Its per-PR gate (`hyperset/evals/report.py`) and its `tools_hash`
  (`sha256:fe930a003b731211`, `hyperset/planner/loop.py`) are untouched by this
  benchmark.
- This benchmark runs on a SCHEDULE or ON DEMAND (never as a required per-PR check),
  the same split ADR 0013 defined for the live Ollama arms.
- No CI job may gate a merge on this benchmark's outcome. A wrapper that made it
  required would violate this invariant and this spec.
- It creates **no second semantic authority**: the answering arms score against the
  governed ground truth and the committed reference answers, not against a model's
  opinion of meaning; the model judge scores QUALITY, and its result never blocks and
  never writes governed context.

## 2. Generate — the adversarial expert persona

A frontier "expert" persona (via the Claude Agent SDK; reuse
`hyperset/planner/claude_runtime.py::ClaudeAgentRuntime` as a new `Runtime`
implementation, `hyperset/planner/runtime.py::Runtime` Protocol) produces novel Q&A the
locked `#25` set never contains.

- **Input the generator sees**: only the served discovery surface — the
  `list_context_catalog` output (all governed domains + their concepts) plus the
  `discover_analytics_context` assist ranking. It does NOT see manifests or answer keys
  beyond what the served contract exposes, so its cases are realistic caller questions.
- **How many cases**: **N = 60** committed Q&A cases per run.
- **Domain selection**: the case corpus MUST span **M ≥ 2 distinct governed domains**
  drawn from the catalog (the committed estate has two today — `revenue` and
  `supply_chain`; the floor is 2, not 3, so a run is valid on today's estate, and it
  rises automatically as more governed domains land — the generator always spans ALL
  available governed domains). A run over fewer than 2 domains is INVALID (§6, disclosed,
  not a pass). Within N: **≥ 20% (≥ 12 cases) are cross-domain** (a join or
  reconciliation spanning two governed domains — countable as `|domains| ≥ 2` on the
  committed case), and **≥ 40% (≥ 24 cases) are "traps"** — one of: ambiguous phrasing,
  a deprecated/prohibited source, a missing required filter, a stale assumption, or an
  unenumerated grain (exactly the failure classes `#122` names).
- **Reference the generator commits per case**: `{question, domains[], trap_type|null,
  reference_answer, expected_governed_refs[]}`. The correctness ORACLE is grounded, not a
  model's opinion: `expected_governed_refs[]` MUST resolve to real governed rows in the
  pinned snapshot (a case whose refs do not resolve is rejected at commit), and the trap
  is a governed, deterministically checkable fact (a prohibited/deprecated source ref, a
  declared required filter, a declared grain). `reference_answer` is an ADVISORY prose
  aid for the judge, never the sole authority. A committed corpus is HUMAN-CURATED before
  it is committed — the generate step is human-authorized exactly as `#25`'s recorded
  runs are — so no model-authored text becomes ground truth unreviewed.
- **Determinism / seeding (reproducibility)**: the generator runs at
  `temperature = 0.0` with a pinned seed (`PINNED_SEED = 20260728`,
  `hyperset/evals/pins.py`) and pinned model tag + digest + prompt hash. Because a
  hosted frontier model is not bit-reproducible even at temperature 0, **the generated
  case corpus is COMMITTED** (a `Recording`-shaped JSON, §6) and every later scoring run
  REPLAYS the committed corpus rather than regenerating — the exact recorded-replay
  split ADR 0013 uses for `#25`. Regeneration is a periodic, human-authorized step that
  writes a new committed corpus with fresh provenance.

## 3. Answer two ways — the exact tool contract per arm

Both arms answer the SAME committed question under the SAME limits: `MAX_TURNS = 8`
(`hyperset/evals/run.py`), the same per-arm answering model, the same token budget. Both
are read-only; neither executes warehouse SQL (permanent v0 invariant, ADR 0032).

- **Hyperset arm** (reuse `hyperset/evals/arms.py::GOVERNED_ARM` +
  `hyperset/planner/loop.py::tool_specs`): the served MCP contract only —
  `list_context_catalog` (discovery), `discover_analytics_context` (assist-class ranking,
  ADR 0022), `resolve_analytics_context` (a `ContextDirective` → `ContextBundle`), and
  `validate_analytics_plan` (deterministic plan check). No raw-lake access. Driven
  through `hyperset/planner/executor.py::InProcessExecutor`.
- **Raw data-lake arm** (reuse `hyperset/evals/raw_arm.py::RAW_TOOL_SPECS` +
  `RawMetadataExecutor`): the two benchmark-only read-only observation-store tools
  `list_raw_assets` / `get_raw_asset`, and NO Hyperset governed context. These tools are
  benchmark-only and are **never mounted on HTTP or MCP** — "giving the baseline the
  product's surface would compare Hyperset to Hyperset" (`raw_arm.py`).

Neither arm's tools are on `RESOLVE_PATH_OPERATIONS`'s hashed allowlist beyond the three
already there, so this benchmark moves **no `tools_hash`** and touches **no committed
`#25` recording**.

## 4. Judge — rubric and numeric aggregate

A frontier expert persona judges. To prevent self-grading, **the judge model MUST be a
different model from BOTH answering models in the comparison it scores** (model-level,
not merely a different prompt or persona instance). It scores both answers **BLIND**: arm
identity hidden and answer order randomized per case. It scores each answer on three
axes, each an **integer 0–4**:

- **Correctness** — factual agreement with the case's grounded oracle: the governed
  context the case's `expected_governed_refs` resolve to in the pinned snapshot, with
  `reference_answer` as an advisory aid (0 = wrong, 4 = exact). The oracle is governed
  data, not the judge's opinion.
- **Evidence / citation quality** — did the answer cite the correct governed sources
  (`expected_governed_refs`) with provenance (0 = none, 4 = exact refs + provenance).
- **Avoided-mistake** — for a trap case, did the answer avoid the embedded trap
  (deprecated/prohibited source, missing filter, stale assumption, wrong grain)? 0 = fell
  in, 4 = avoided and said why. For a non-trap case this axis scores whether the answer
  introduced no unforced governance error. **Cut points used by §6.3**: an answer
  **AVOIDED** the trap iff this axis is **≥ 3**; it **FELL IN** iff this axis is **≤ 1**.

**Per-answer score** = sum of the three axes, integer **0–12**. **Hyperset advantage on
a case×arm** = `score(Hyperset) − score(raw)`, range −12…+12.

- **Judge determinism**: the judge runs at `temperature = 0.0` with a pinned model tag +
  digest + prompt hash + seed. A judge-stability re-score of the same answers MUST agree
  within **±1 on ≥ 90% of axis scores**; a run failing this is INVALID (§6).

## 5. Model arms

Run generate/answer/judge across **K ≥ 2 answering models**, the same
`ArmSpec`-per-model shape as `#25` generalized past its single locked model
(`hyperset/evals/arms.py`):

- the pinned small local model `qwen2.5:7b` at a 32,768-token window
  (`hyperset/planner/runtime.py::PINNED_MODEL`, `CONTEXT_WINDOW_TOKENS`), and
- at least one pinned frontier model.

Each answering model runs BOTH the Hyperset arm and the raw arm (so the comparison holds
the model fixed and varies only Hyperset-vs-raw). The generator and the judge are their
own pinned frontier personas, distinct from the answering models being compared.

## 6. Completion criteria — the exit gate

A run is scored over **N ≥ 60 cases × M ≥ 2 domains × K ≥ 2 answering models**
(≥ 120 case×arm answer pairs). The benchmark **PASSES** iff ALL of:

1. **Aggregate advantage** — the mean Hyperset advantage (per-answer 0–12 scale) across
   all case×arm pairs is **≥ +2.0**, AND the lower bound of a **deterministic bootstrap
   95% confidence interval is > 0** (a real signal, not judge noise). The bootstrap is
   fully specified so the report recomputes it identically: **10,000 resamples**, the
   **percentile** method, **resampling clustered by case** (each resample draws whole
   cases with replacement, so the K arms of a case move together and the CI is not
   inflated by treating correlated arm scores as independent), seeded with
   `PINNED_SEED = 20260728`.
2. **Pairwise win rate** — Hyperset scores **strictly higher** than raw on **≥ 65%** of
   case×arm pairs (a tie, equal scores, counts as NOT a win).
3. **Avoided-mistake capture** — over the trap cases where the RAW arm **fell in**
   (avoided-mistake axis ≤ 1, §4), the Hyperset arm **avoided** (axis ≥ 3) on **≥ 80%**.
   This criterion requires a denominator of **≥ 10** such cases; below 10 it is DISCLOSED
   as UNMEASURED (neither a pass nor a fail of criterion 3, and the run is still judged on
   criteria 1–2), never a divide-by-zero pass.

**Validity guards** (a run that fails any of these is INVALID and DISCLOSED, neither a
pass nor a merge-blocker): N ≥ 60; M ≥ 2; K ≥ 2; cross-domain ≥ 12 and trap ≥ 24 cases;
judge stability ≥ 90% axis-agreement within ±1; blind + order-randomized + no-self-judge
(model-level, §4) enforced.

**How a run is recorded** (reuse `hyperset/evals/recording.py` + `provenance.py`): a run
is a committed `Recording`-shaped JSON carrying its provenance the same way a `#25`
recording does — `git_commit`, the measured **tree oid** (the `tree_id` from the
`HYPERSET-GATE v2` line, `scripts/gate.py`), `recorded_at`, the embedded `DISCLOSURE`
sentence (`recording.py`), and full pins: for EACH pinned model (generator, judge, and
every answering model) the **nine** pins `hyperset/evals/pins.py::RunPins` defines —
`model` tag, `digest`, `quantization`, `ollama_version` (or `"hosted"` for a frontier
model), `context_window`, `prompt_hash`, `tools_hash`, `seed`, `temperature` — one set
per model. `tools_hash` is the hash of THAT persona's tool set: the served
`RESOLVE_PATH_OPERATIONS` hash for the Hyperset answering arm, the `RAW_TOOL_SPECS` hash
for the raw arm, the discovery-tools hash for the generator, and a fixed
empty-tool-set sentinel for the tool-less judge. The corpus, both
arms' answers, and every per-axis judge score are stored so the report step recomputes
the criteria deterministically from the committed evidence. Provenance is established
once per session via `hyperset/evals/provenance.py::recording_session()`, which refuses a
dirty tree or a commit off the default branch — "a committed recording is only evidence
about the commit that scores it". A public "Hyperset beats raw" claim additionally
requires a fresh authorized run with these exact pins and full disclosure, exactly as
`#25` requires for its frontier arm.

## 7. Relationship to other work

- **Validates `#122` (the assist epic).** `#122` reopens the covered/uncovered boundary
  so Hyperset helps with the ~90% of questions a manifest does not enumerate. This
  benchmark is the instrument that MEASURES that help: its trap and cross-domain cases
  are precisely the uncovered-majority questions, and its aggregate advantage is the
  evidence that assist mode earns its keep without regressing the governed slice.
- **Mirrors `#96`.** `#96` adds an agent-driven eval layer over the live compose stack
  with the same "informs product decisions, does not block a merge" posture. This
  benchmark is that layer for the adversarial-breadth question, sharing the ADR 0013
  disclosure-and-schedule contract; the two differ only in what they stress (compose
  platform breadth vs adversarial question breadth).

## Reuse summary (no new framework)

`inspect_ai` harness (`hyperset/evals/task.py`); `Recording`/pins/`DISCLOSURE`
(`recording.py`, `pins.py`); provenance/refusals (`provenance.py`); the raw arm
(`raw_arm.py`); the arm shape (`arms.py`); the served MCP contract + `InProcessExecutor`
(`loop.py`, `executor.py`); the frontier generator/judge as new `Runtime` impls over the
existing Claude/OpenAI adapters (`claude_runtime.py`, `openai_runtime.py`,
`runtime.py::Runtime`). New code the implementer adds is confined to: a benchmark corpus
`Recording` superset (adds generator/judge pins + the corpus + per-axis scores), the
generator and judge `Runtime`s, and a NON-BLOCKING `inspect_ai` task + report — none of
it served, none of it on `RESOLVE_PATH_OPERATIONS`, none of it touching `#25`.
