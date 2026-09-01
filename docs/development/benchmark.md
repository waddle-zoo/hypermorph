# The benchmark (GitHub #25)

> [!NOTE]
> **Benchmark-only runtime.** Ollama and Qwen in this document are isolated
> reproducibility fixtures, not dependencies of the shipped demo or served
> product. Chat, authoring, and embeddings use the OpenAI/Luna configuration.

Two local arms answer the same locked questions with the same model, runtime,
seed, temperature, window and turn budget. They differ in one thing: arm 1 gets
Hyperset's governed context through the three served tools, arm 2 gets raw
observed Superset and DataHub metadata. Deterministic scorers read the traces.

## What runs where, and what each half proves

| | Required, per pull request | Scheduled, weekly |
| --- | --- | --- |
| Command | `hyperset evals score` | `pytest tests/evals` |
| Needs | nothing but this repository | a real Ollama and Postgres |
| Scores | committed **recordings** of both arms | a live model, right now |
| Proves | the harness, the scorers, and the recorded run's scores | that a live model still passes |

The split is ADR 0013. One happy-path run of the pinned model measured CPU-only
is 314 seconds, so a required gate running the arms live is 1.6 to 3.2 hours on
a shared cloud vCPU. Every report says which half produced it; a scheduled
failure does not block a merge by itself.

A committed recording is only evidence about the commit that scores it. It
carries the prompt hash, tool-schema hash, model tag, observed window, seed and
temperature it ran under, and `hyperset evals score` compares all six to what
the repository says today. It also carries `task_version`, the content hash of
the case file, which is compared the same way. **Editing the planner prompt, a
tool description or a case invalidates every recording**, and the gate fails
until they are re-made -- that is the check working, not a chore to route
around.

The scheduled half sets `HYPERSET_REQUIRE_LIVE=1`, which turns a session that
ran no arm red. Without it an unreachable Ollama skips every case and exits 0,
and a green with no live run is indistinguishable from a green with one --
which would leave the only thing that checks a live model unable to fail
(hy-26nd).

## Running the live arms

The arms need the pinned window actually served. Ollama's OpenAI-compatible
endpoint ignores a requested context size, so it is set on the server:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve
ollama pull qwen2.5:7b
uv run pytest tests/evals -q -s
```

`observe_pins` refuses to start a run whose window nobody observed, and
`assert_pins` refuses one whose observed window is not the pinned number --
EQUALITY, not a floor, so 65,536 fails exactly as 4,096 does. Either way a
mis-served model fails before the first token rather than being silently
truncated.

### When something else already owns 11434 (hy-z8dd)

The recipe above assumes the port is free. The Ollama desktop app holds 11434
whenever it is running and allocates a 4,096-token window, which no environment
variable in the shell reaches, so the run dies before the first token with:

```text
hyperset.evals.pins.PinMismatch: pinned run does not match its pins -- context_window: expected 32768, got 4096
```

That is the pin guard working. The remedy is a second server on another port,
which is how every recording committed so far was produced:

```bash
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_HOST=127.0.0.1:11435 ollama serve
HYPERSET_OLLAMA_BASE_URL=http://127.0.0.1:11435/v1 uv run pytest tests/evals -q -s
```

`HYPERSET_OLLAMA_BASE_URL` is the supported way to point the suite at a server
other than `http://127.0.0.1:11434/v1`. Quitting the desktop app works too and
needs no variable.

**Which server produced a recording is already answered, and not by a URL.**
`context_window` is compared for equality before the first token, so a run
against the desktop app's 4,096 window never becomes a recording -- it raises.
Every persisted recording therefore comes from a server that allocated exactly
the pinned window, whatever port it listened on, and a 4,096 in a terminal is
always a failed run rather than an artifact somebody has to tell apart from a
good one.

To refresh the committed recordings, set `HYPERSET_RECORD=1`. It is off by
default and off in the scheduled workflow: a benchmark that rewrites its own
evidence whenever it runs cannot detect a regression.

### What a recording session demands of the tree (hy-r1i0)

**Commit first, then record.** `run_case` refuses to start from a dirty tree,
observes `HEAD` once per process, and refuses again if `HEAD` moves before a
later case — an auto-checkpoint hook, a rebase in a worktree sharing the
repository, an agent. `write_recording` then refuses to persist a recording
whose `git_commit` is reachable from no ref. Together they mean the commit on a
recording is a commit somebody else can look up, which is what makes "a
committed recording is only evidence about the commit that scores it" a
statement rather than a hope.

The measurement behind the rule: one 17-minute session over four case/arm pairs
wrote **two** commits, neither the commit under test, because a checkpoint hook
committed twice mid-run; squashing those checkpoints away left three of the four
recordings pinned to objects reachable from no branch. The stability report
could not see it — it compares `git_commit` across the repetitions of one case,
and each case's three fell inside one checkpoint window. If a session refuses
with `CommitMoved`, stop whatever is committing and re-record; do not re-run and
hope the window is quiet.

## The stability report

Every case runs three times per arm, and the repetitions are compared
(`HYPERSET_STABILITY_REPETITIONS` overrides the count). #25 asks the harness to
report its stability rather than assert it, so this is a REPORT: no threshold,
no CI job, nothing that can fail a build. Each case prints one line and, when
something disagreed, the exact answers that disagreed:

```text
HYPERSET-STABILITY v2 sha=<40-char> arm=<arm> case=<case> n=<N> model=<tag> digest=<digest> ollama_version=<version> context_window=<tokens> seed=<seed> temperature=<t> prompt_hash=<hash> tools_hash=<hash> predicates=<P> unanimous=<U> flapping=<F> answers_distinct=<D> source_refs_distinct=<S> source_versions_distinct=<V> unversioned=<W> trace_shapes_distinct=<T> verdicts=<name:verdicts,...> traces=<shape,...> answers_id=<hash> evidence_id=<hash> result=REPORT-ONLY
```

Quote the line whole beside any stability claim, for the reason the gate's
evidence line exists: the number means nothing without the pins it was taken
under.

**What the repetitions hold fixed, and therefore what the number measures.**
Every repetition runs one commit, one case file, one prompt hash, one
tool-schema hash, one digest, one seed and one temperature; a report whose
repetitions disagree on any pin is refused rather than averaged. `git_commit`
and `task_version` are not `RunPins` fields, so they are compared across
repetitions in `stability_report` and a drift raises `PinsDrifted`. Three
commits in one report used to be accepted with the line naming the first.

**What "reproduces" means, and what it does not.** The comparison is over
predicate verdicts, answer text, the evidence — at both identities described
below — and, since **hy-9dyv**, the ordered trace shape. It is not over anything
else: no step timing, no parameters, no tool result payload.

`trace_shapes_distinct` is that fourth axis, and it is the one field on the line
that is **not** normalised to a set. The questions it exists for are whether the
catalog came before the resolve and whether validate was called at all, so
`catalog>resolve` and `resolve>catalog` are two shapes and a call made twice is
not a call made once. Everything else about a trace is dropped, including the
per-run timestamp every step carries — which is what made "the same trace" a
design question rather than a hash. A refused call keeps its operation with `!`
in front, because a call the server would not answer is a different run from one
that was never made. `flapping=0` on a v2 line therefore says something about
the tools that a v1 line did not, which is half the reason for the version.

It does **not** measure roll-to-roll variance. A cosmetic prompt or
tool-schema edit moves `prompt_hash`/`tools_hash` and re-rolls the arm
(**hy-hk5m**), so repeating with those varied would measure the re-roll rather
than the substrate. That question is open and this report does not answer it.

**The four identity fields, and why counts were not enough.** `verdicts`,
`traces`, `answers_id` and `evidence_id` say *what* a session saw on each axis;
every count beside them says only whether the session held together internally.
That distinction is not theoretical: hy-hk5m recorded three sessions at one tree
and one set of pins where one called validate and failed two lexical predicates
and two never called it and passed them — each internally unanimous, each
printing `flapping=0`, and every count on a v1 line identical. `verdicts` and
`traces` are legible so a reader sees which predicate and which shape moved;
`answers_id` and `evidence_id` are hashes because their content is unbounded,
and the rendered report prints that content underneath.

`answers_distinct` is separate from `flapping` on purpose: the predicates are
lexical, so two differently worded answers can score identically, and a report
comparing only verdicts would call that stable.

`source_refs_distinct` is separate from both for the same reason one layer down:
two repetitions can agree on every verdict AND on the answer text while resting
on different evidence, which was measured on this report and invisible in it. It
counts distinct `source_refs` **sets** — normalised, so ref order and a repeated
ref are not instability — and the report names the refs even when nothing moved,
because two reports that each held steady on different sources are not the same
result. An empty set prints as `<none>`; the committed recordings carry four refs
per governed case and two per raw-baseline case, so `<none>` on a governed line
is itself a finding.

`source_refs_distinct` identifies an **asset**, never an asset **version**
(**hy-o79s**). A re-observation of one source between two repetitions — new
`observed_version`, new `content_sha256`, the same ref — is the same set, so on
its own that field would call two repetitions that read different bytes
identical evidence. It is not a labelling problem: the governed context is read
from the store per repetition, so a connector run or an approval landing
mid-record gives repetition 2 a different bundle, and the flap would print
against the model.

`source_versions_distinct` is that finer identity: the `(ref, content_sha256)`
**pair** per observed asset, derived for the report from the trace the recording
already persisted, and nothing re-reads an asset.

**Both counts come off one walk of one payload** (**hy-szg4**), which is what
makes them comparable. `stability_report` walks each recording's trace once and
each repetition holds that one list of `(ref, identity)` pairs, so the two
fields are two views of one set rather than two answers to one question. The
earlier arrangement took refs from the persisted `Recording.source_refs` and
versions from a separate walk, and a probe printed `source_refs_distinct=2
source_versions_distinct=1` out of it — the refinement inverted. The persisted
field is not redefined: #25 requires a recording to carry it, so it is compared
against the walk, and a recording that disagrees with its own trace raises
`EvidenceMismatch` instead of being quietly re-derived.

So read the two counts as a pair. They are a refinement, and the refinement holds
**by type** (**hy-q2mn**): both projections group on the same walked list, the
coarse one keyed by `ref` and the finer one by the whole `(ref, identity)` pair,
so a ref set is the image of a pair set under `ref` and versions is never fewer
than refs. The world the pair exists for is `source_refs_distinct=1
source_versions_distinct=2`: one asset, two versions of it. The rendered report
prints the version sets when they disagree and a hash of the set when they do
not, for the same reason it prints the refs every time — two reports steady on
different versions are not the same result.

That claim was false before it was true, which is why it is stated by type here.
The finer projection used to group on a joined `ref@identity` **string**, and
that join is not injective — both halves are arbitrary payload strings, so `@` is
legal in either and `('dataset:a@b', 'c')` and `('dataset:a', 'b@c')` were one
key. Two repetitions holding those printed `source_refs_distinct=2
source_versions_distinct=1` and rendered `EVIDENCE 2 distinct sets across 2`
directly above `VERSIONS identical`. The join now happens only when a token is
rendered for a human, after every count is taken. The bound, since it decides how
much it mattered: the identity must contain `@`, no producer in this repository
writes one, and all four committed recordings walk to entries with `@` in neither
half — so what was defective was the universal claim, not a number this
repository has produced. `EvidenceMismatch` does not cover it, because that
compares refs only.

`unversioned` is how much of that comparison happened at all. `content_sha256`
is `None` for an asset the store holds with no current version, the walk gives
that entry the identity `unversioned`, and a run whose evidence is entirely
unversioned would otherwise print `VERSIONS identical` with a hash beside it —
agreement reported from nothing, which is the defect the fixture secret scan had
one module over (**hy-jnem**). So the count is on the line, the render says
`VERSIONS vacuous` (or `VERSIONS none` for a run that recorded no evidence at
all) rather than `identical`, and the refs that carried no version are named.
Both the count and that sentence test the **identity** rather than a suffix of
the rendered token (**hy-q2mn**): while the render asked whether every token
ended in `@unversioned`, an identity that merely ended in the marker produced a
report whose line said `unversioned=0` above prose claiming every entry was
unversioned.
There is nothing finer to fall back to: `observed_version` is read off the same
`asset.current_version` the hash is, so it is `None` in the same payload.

Its bounds are the same walk's bounds. An asset named under any key other than
`linked_evidence.observed_assets[].ref` or the raw arm's `get_raw_asset`
`external_id` contributes no ref and therefore no version. On the raw arm the
identity is a content hash of the `raw_payload` that tool returned, which is
**version-level with the store's narrowing** rather than byte-level: the tool
serves `asset.current_version.raw_payload`, and the store writes a new version
only when the hash over the `hash_basis`-narrowed payload moves. A re-sync that
touches only Superset's `*_humanized` relative times, or that reorders a DataHub
`customProperties` map, therefore writes no version, leaves the bytes this arm
returns untouched, and is invisible at both identities.

`HYPERSET_RECORD=1` writes the first repetition, and it writes it **after** the
cross-repetition check has accepted the set. Every repetition asserts the six
repository pins by value on its own, but digest, quantization and Ollama
version are only checked for presence, so a model re-pull or a server upgrade
mid-run is drift that the comparison is the first thing to see — and recording
before it ran would leave a refreshed recording on disk behind a red run.

## Comparing two sessions (hy-hk5m)

Everything above compares repetitions inside one process. It cannot answer the
question that decides the gate's colour: does a session reproduce *another
session*?

The measurement that made this a decision rather than a disclosure: one tree,
one model, one digest, one window, one seed, one temperature, one prompt hash
and one tool-schema hash; three sessions; two behaviours. One session called
`validate_analytics_plan`, passed `plan_validated_before_the_answer` and failed
`evidence_cited` and `governed_rules_stated`. Two sessions never called validate
and inverted all three. Each was internally unanimous and printed `flapping=0`.
What separates them is session composition — four cases in one process against
one — and warm server state, and neither is a pin. The consequence is that the
required gate's colour is a function of which session got recorded.

"Re-record until green" was available on top of that, and hy-xfhr closed the
part of it that adding files could reach. A declared entry is now compared to
the shape it claims over the stored runs, so committing another session cannot
clear a red an improvement raised: under `every` a retained pre-fix recording
leaves the entry outdated rather than alive, and under `some` the corpus has to
match the declared rate exactly. What remains, stated rather than called shut:
**deleting** a recording still moves the colour, and a `some` entry declaring
more runs than the corpus holds can be reached by recording until the counts
line up. Both are edits somebody can see — a deleted file, or a rate restated
in `expected_failures.yaml` — which is the whole of what this mechanism buys.

`compare_sessions` reads pasted v2 lines and answers with one of **three**
outcomes:

```text
HYPERSET-CROSS-SESSION v1 sessions=<N> outcome=AGREE|DISAGREE|CANNOT-COMPARE ...
```

`CANNOT-COMPARE` is a first-class answer and never a quiet `AGREE`: a
two-valued instrument says the safe word when it could not look, and here the
safe word would be "stable". A v1 line is refused **by its version** rather than
by a missing field, because an absent `answers_id` compared against another
absent `answers_id` is silence read as assent. Lines whose pins differ are
refused the same way — the cross-session form of `PinsDrifted`.

`sha` is in that comparability key, so two sessions at two commits are
`CANNOT-COMPARE`. That is deliberate and it is the strict choice: any code
change between two trees is a standing alternative explanation for a
disagreement, and `SCHEMA_VERSION` is not a repository pin (**hy-5e19**), so
response prose the model reads can change with no pin moving at all.

**Two sessions of one case at one tree can be stored** (**hy-qc4u**).
`recording_path` is `recordings/<arm>/<case>/<run_id>.json` — the store is keyed
on the run, so a second session of a case is a second file beside the first
rather than an overwrite, and every stored run is scored. Until that bead it was
one file per arm and case, overwritten on every record, and the comparator's own
fixtures predate the fix: they are assembled from recordings taken at three
different commits with the commit equalised in one named test helper, because
that is where a real divergence was found and not because a pair at one tree
could not be held. Those fixtures are evidence about the comparator and **not**
about the substrate; they must not reach #25's release sheet as a measurement.

What the store can now hold and nothing has yet run is n per case and
cross-session variance at one tree (**hy-pgtt**). Storable is not measured.

## Inspect AI

The same recordings are also scored as an Inspect task, which is where the run
log and the sample-level report live:

```bash
uv run inspect eval hyperset/evals/task.py --model mockllm/model
```

The model argument is Inspect's, not the task's. The solver replays a recording
and never generates.

## The recorded result at this commit

Live `qwen2.5:7b`, Ollama 0.32.4, observed 32,768-token window, seed 20260728,
temperature 0, against the real revenue slice. Re-recorded on the default-deny
change (hy-9nrf), which put the unknown-value rule into the planner prompt and
all three tool descriptions and moved both pins -- `prompt_hash`
`sha256:6b37ec8642a6c2f9` to `sha256:b16998b299c0837a`, `tools_hash`
`sha256:45c0b63b03059f77` to `sha256:78c43e97e41d4bf7` -- and therefore
invalidated the recordings made on the coverage-claim change (hy-9lct). Only
the governed arm was re-rolled: the raw baseline reads its own prompt and its
own tool specs, neither of which this change touches, so its recordings are
still evidence about this commit and re-running them would have been a second
sample of nothing. Every number below survived the re-roll unchanged, which is
a fact about this run rather than a property of the harness (hy-hk5m: a 7B model
at temperature 0 is a deterministic function of its input bytes, and these bytes
moved). Shared predicate set:

| case | governed | raw baseline |
| --- | --- | --- |
| `revenue_by_region` | 4/4 | 2/4 -- cited neither required dataset, stated neither the completed-order filter nor the test-account exclusion |
| `supply_chain_lead_time` | 2/2 | 2/2 |

Governed-only predicates, 3/5. Those are five predicate INSTANCES over two
cases, not five distinct guarantees: `catalog_before_resolve` twice (pass,
pass), `directive_named_the_expected_domain` once (pass),
`plan_validated_before_the_answer` once (fail),
`no_governed_answer_without_a_governed_domain` once (fail).

**Exactly one critical structural guarantee is both exercised and passing**, and
saying "fails two of its own five" reads better than the measurement supports
(hy-sszk). `catalog_before_resolve` is non-critical and can never redden the
gate. The sixth governed-only predicate, `unfixable_ref_not_retried`, returns
nothing on every committed recording -- no case induces a `ref_not_observed`, so
it is **declared and unmeasured**: its only evidence is a unit test against a
synthetic trace, which is coverage of the predicate and not measurement of the
arm. The case that would measure it -- a question that leads the arm to ask for
a ref nothing observed -- is filed as hy-815z rather than written here, because
a case is an edit to `hyperset/evals/cases/revenue.yaml`, that file is
content-hashed into `task_version`, and such an edit invalidates all four
committed recordings and demands a live re-record of both arms. That is the
freeze below working, not an evasion: it belongs to the next pull request that
re-records. Measuring it also wants hy-rvh1 first -- the `ref_not_observed`
warning names its ref only in prose, so the predicate can currently check only
"sent no ref twice after any such disclosure", not "stopped asking for THAT
ref".

What the recordings therefore support is one demonstrated
critical pass, `directive_named_the_expected_domain` on the single
`governed_fetch` case, against two demonstrated critical failures. Both failures
are filed, both are P1, and neither predicate was weakened:

- `revenue_by_region`, `plan_validated_before_the_answer`
  (**fix: hy-3dtc, hy-1r0h**): the arm resolved governed context, DID call
  `validate_analytics_plan`, and the call came back `unverifiable`. It sent the
  plan's two dataset refs in the directive's `asset_refs` where the bundle's own
  request echoes an empty list, so the call re-resolved to a different bundle
  and the plan was never judged. This is the third mechanism this one predicate
  has failed by: PR #103 recorded a double call failing on an empty
  `source_refs` and a stale `bundle_id`, the re-record before this one recorded
  no call at all, and hy-t3am fixed the served contract underneath both. Those
  earlier contract defects are hy-pvbu's, which closed 2026-07-30 with no
  committed recording exercising them; what retires the entry now is whichever
  of hy-3dtc and hy-1r0h lands last.
- `supply_chain_lead_time`, `no_governed_answer_without_a_governed_domain`
  (**limit: ADR 0016**): the governed path resolved the `revenue` domain for a
  supplier-lead-time question, again -- but by a different route. The directive
  now has to carry a coverage claim, and the model made a FALSE one, claiming
  `recognized_revenue` covers supplier lead time. See below.

Every number in this section is parsed out of it and compared to what
`score_recordings()` emits by
`tests/unit/evals/test_benchmark_md_matches_the_scored_recordings.py` (hy-rd7e)
-- the table cells, the headline pair, the governed-only fraction and its
instance breakdown, and the two failures above against
`expected_failures.yaml`. It parses rather than searches, because a check
looking for the substring `6/6` is green on a document that also still says
something else two paragraphs later, and it refuses any fraction here that no
arm scored. A re-record therefore turns the test suite red until this section
is rewritten, which is the point: the numbers below are what everyone quotes.

Read those against the shared-set numbers above: on every predicate both arms
could attempt, the governed arm did not lose to the baseline. What failed is the
governed path's own additional promises, which is the most useful failure this
benchmark can produce -- it is the substrate's own contract, measured.

### What the coverage claim changed, and what it did not

`ContextDirective.concepts` is required with `domains`, and a term the domain's
Git context does not declare is refused with `domain_does_not_declare` (hy-9lct,
`docs/v0-foundation.md` section 7). Measured against these recordings:

- What it closed: a domain named with NO claim can no longer produce governed
  context. That is exactly the move PR #103 recorded, and the refusal is in the
  substrate rather than in a prompt line, so deleting the prompt line reproduces
  a refusal.
- What it did not close: the predicate still fails. The model claimed
  `recognized_revenue` for a supplier-lead-time question and was served, because
  the claim is true of the DOMAIN and false of the QUESTION, and only the
  question can tell them apart. Hyperset does not read the question -- that is
  the routing GitHub #70 deleted -- so no check it is allowed to run contradicts
  a false claim.

The honest statement of the guarantee is therefore narrower than the predicate's
name: no governed answer without a DECLARED, VERIFIED coverage claim. Making the
predicate itself true by construction requires either question interpretation
inside Hyperset or a different mechanism entirely, and that is an ADR-level
decision rather than a fix, and ADR 0016 is where it was taken. The entry stays
 declared in the ratchet and does not expire; hy-9lct, closed 2026-07-29, owns
 the record of the change rather than a pending fix.

### What the headline number summarises, and what it does not

6/6 against 4/6 is six predicate instances per arm, and four of them --
`run_completed` and `prohibited_source_avoided`, on both cases -- are passed by
**both** arms. Those four are the raw baseline's entire 4/6. The whole delta is
`evidence_cited` and `governed_rules_stated` on `revenue_by_region`: one case,
two predicates, n = 1. A second domain and more cases are filed (hy-pt9v,
hy-esp); until they exist this is one measured question, not a result about a
substrate (hy-axg9).

Both predicates carrying that delta are **lexical**: they ask whether an exact
string is present in what the arm said, and read no part of the trace.
`evidence_cited` asks whether the two dataset UUIDs appear in the answer;
`governed_rules_stated` asks whether the three rule strings do. The predicates
that read behaviour -- which tool was called, in what order, with what
parameters, and what came back -- are `run_completed`, `catalog_before_resolve`,
`directive_named_the_expected_domain`, `plan_validated_before_the_answer`,
`unfixable_ref_not_retried` and `no_governed_answer_without_a_governed_domain`,
and five of those six are governed-only, so no behavioural difference between
the arms is in the number. (`run_completed` is behavioural in the weakest sense
-- it reads whether the run died and whether the arm said anything at all,
never what it said. `prohibited_source_avoided` is both: it reads the answer
text AND the call parameters, and it passes on both arms on both cases, so it
contributes nothing to the delta either way.) The delta is two string-presence
checks on one case.

`governed_rules_stated` passing is also weaker than it reads. The `4/4` in the
table above is one run, and it PASSES on that same governed recording where
`plan_validated_before_the_answer` FAILS -- the shared set scores a run that
never submitted a plan for validation at all. All three required strings --
`SUM(gross_amount - tax_amount)`, `status = 'completed'`, `is_test = false` --
appear verbatim in that arm's own tool results before it answered. So the route
to the pass is copy-out: the governed
substrate put the rules in front of the model and the model repeated them. That
the model APPLIED them is not what this predicate shows, and nothing this run
did with the rules was ever checked against a plan. The predicate is not
weakened here; a lexical check is what #25 asks for over a model judging a
model, and what it measures belongs beside the number rather than inside it.

### The ratchet

`hyperset evals score` compares the governed arm's critical failures to
`hyperset/evals/expected_failures.yaml` and exits 0 only on an **exact** match.

- A failure nobody declared is red, so a regression cannot hide behind a known
  defect.
- A declared failure that stops failing is **also** red. A defect the stored
  runs stop exhibiting means deleting its entry in the same pull request,
  because the gate refuses to pass while it claims a defect that is gone. That
  holds for the `limit` row too: `limit` says no patch retires the entry, not
  that the entry is exempt from the evidence.
- A declaration the stored runs no longer **match** is red as well, and this is
  the direction that keeps the second one reachable once a case holds more than
  one run (hy-xfhr).

Each entry is one case id, one predicate name, one line of measured reason, a
required **shape** with no default (ruling hy-5e1p) and a required
**retirement** with no default (hy-p8k5):

- `every` — every stored run of the case exhibits the defect. Another failing
  run needs no edit; one run that does not exhibit it is red. The supply-chain
  row is `every`: ADR 0016 holds it as an architectural limit, not a flake.
- `some` — the defect is intermittent, and the entry carries the measurement it
  rests on, `runs_exhibiting` of `runs_scored`. A corpus that no longer shows
  those numbers is red until the file states what it does show. The revenue row
  is `some`, measured at 1 of 1 over the committed runs.

The retirement field exists because both entries used to name a bead that had
already closed — hy-pvbu 2026-07-30, hy-9lct 2026-07-29 — so no fix could delete
either one and nothing in the harness could see it:

- `fix` — a defect awaiting a patch. `beads` lists the OPEN work whose landing
  deletes the entry, as a list, because retirement is not always one bead's to
  do. `scripts/check_expected_failure_owners.py` asks `bd` whether they are
  still open; it is a script and not a test because the forge does not install
  `bd`, and a guard that skips on CI is the same green as no guard.
- `limit` — an architectural limit no patch retires. `adr` names the accepted
  decision that says so, and the loader refuses unless that ADR exists, still
  says `Status: accepted`, and names BOTH this case id and this predicate.
  Without that last check `limit` would be a downgrade any accepted decision
  could rubber-stamp, and it is the cheap branch: nothing about it has to stay
  open.

The counts are over **repetition zero of each committed session**, because
`stability.repeat_and_report` compares N repetitions and commits
`recordings[0]`. That sample is selected rather than random — index 0 is the
coldest repetition of its session — so a rate quoted from a stability report is
a different population and does not go in that file.

There is no pattern syntax and no arm field: a wildcard would be the demotion
this mechanism exists instead of, and `tests/unit/evals/test_the_ratchet.py`
asserts the file holds only these two entries, that both name governed-only
predicates, that a malformed declaration is refused at load by file and entry
name, and that the gate turns red in all three directions — including on one
corpus scored twice, where `every` stays green and a stale `some` rate does
not.

## How the arms are compared

The headline number is the **shared predicate set** -- `run_completed`,
`prohibited_source_avoided`, `evidence_cited`, `governed_rules_stated` -- and
only that set. Those are the predicates both arms can attempt, so a difference
between the arms on them is a difference in what the substrate gave the model.

The governed-only predicates -- did it list the catalog, did it name exactly one
domain, did it validate against the bundle it resolved, did it stop re-asking
for an unobserved ref, did it refuse to resolve for a question nothing governs
-- are reported beside the headline as capability of the governed path. They are
not folded into the same fraction: arm 2 has no catalog, no directive and no
plan check, so a combined number would score the two arms over different
denominators and call the quotient a delta.

`governed_rules_stated` is deliberately in the SHARED set. Nothing stops a model
from reading a tax split out of a payload that defines one; that raw Superset
metadata defines none is the substrate difference this benchmark exists to
measure, so moving that predicate out would delete the finding.

## The freeze

Prompts and predicates both changed in response to observed output during
hy-ast: three revisions per arm, and two scorer corrections (one of which had
been running in arm 2's favour). That was development, and it stops here.

**From the commit that recorded these runs, any change to either arm's prompt,
to a predicate, or to a case invalidates every committed recording and requires
re-recording every arm from scratch.** Adding an entry to
`expected_failures.yaml` is the same kind of change and belongs to the same
rule: it accepts a measured defect, names the bead that owns the fix, and is
deleted by that fix rather than by a later reading of the file.

Two of the three halves are mechanical. An edited prompt or tool description
fails `hyperset evals score` on the pin check, and an edited case fails it on
`task_version` (hy-j3ms). The case half was prose here until that check
existed, and prose was not enough: emptying `must_state` and `must_cite` on
`revenue_by_region` and scoring the unchanged recordings tied both arms at 4/4
shared with a green gate. What remains prose is a predicate quietly loosened
until an arm passes it -- the scorers are code, not data, so no content hash
sees them (hy-5pa7 is the bead for pinning them too).

Numbers produced by unlogged iteration against the scorer are not evidence. A
revision after the freeze belongs in a pull request that says which arm changed,
on what observed failure, and shows both arms re-recorded.

## The prompt asymmetry, stated rather than hidden

Both arms' prompts were revised in response to their own recorded failures, and
the count is part of the result:

| | revisions | driven by |
| --- | --- | --- |
| arm 1, governed | 3 | skipped `validate_analytics_plan`; resolved the nearest domain for a question nothing governs; re-validated a `warnings` plan instead of reporting its gaps |
| arm 2, raw baseline | 3 | used a metric name as if it were a column; answered from a related-sounding dataset; named fields it had not fetched |

A CORRECTION to that row, which said the raw arm "invented
`SUM(recognized_revenue)`, which no payload defines" (hy-ytp1). The committed
recording shows something more specific, and the first half of it matters:
`get_raw_asset` served the metric `recognized_revenue` **with** its expression
`SUM(gross_amount - tax_amount)`, and the arm quoted that expression back in its
answer -- which is why it passes that one string. It then wrote
`SUM(f.recognized_revenue)` in its SQL, treating the metric's name as a column
of the table, which is the defect the row is now worded for. Neither is the
scored failure: that is `did not state status = 'completed'; is_test = false`.
The finding survives the correction and is sharper for it -- the raw arm could
read the tax-net expression out of Superset's own metadata and still could not
know the two filters, because no raw payload carries them.

The same wrong claim is in a comment in `hyperset/evals/cases/revenue.yaml`
("only the governed context says revenue is recognized net of tax"). It is left
standing on purpose: that file is content-hashed into `task_version`, so
correcting a comment in it invalidates all four recordings and demands a
re-record. hy-ytp1 stays open for it, to be fixed in whichever pull request
next re-records the arms.

That symmetry is deliberate and it is the honest framing of the comparison.
Arm 1 was tuned first, which made the delta "governed substrate plus more
prompt effort" rather than "governed substrate" until arm 2 received the same
treatment: an explicit stop-if-not-covered rule, an explicit
check-before-answering step, and an explicit prohibition on inventing an
expression. Each is the raw arm's counterpart of a rule arm 1 has.

What is NOT equalised, because it cannot be: arm 1 can check a plan against
governed context with a tool, and arm 2 can only be told to re-read the payloads
it fetched. That difference is the substrate, which is the variable under test.

A future prompt revision to one arm without the same scrutiny of the other
reopens this, and the number above is what a reader should check first when a
delta moves.

## What the scorers may read

Structure -- which tools were called, in what order, with what parameters, and
what came back -- and whether an exact identifier appears in what the arm said.
Nothing reads English prose for meaning, and there is no model judging another
model's answer. Whether an explanation was *good* is what #25 leaves to blind
human review of representative traces.

Predicates are keyed to identifiers both arms can produce. The governed arm
cites `superset:dataset:<uuid>`; the raw arm sees the same dataset under the
same UUID. A predicate keyed to the governed prefix would score the substrate
rather than the answer, and arm 2 would fail it by construction.
