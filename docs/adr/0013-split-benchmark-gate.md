# 0013: Split the benchmark gate — recorded traces per PR, live local arms on a schedule

Status: accepted.

## Context

`#25` said "Required CI runs arms 1-2 without hosted credentials". Arms 1 and 2
are the local-model arms, so that sentence made a required gate run a local
model on whatever hardware CI provides. GitHub Actions `ubuntu-latest` is
4 vCPU with no GPU, while every context-budget number this project has reasoned
with was measured on an Apple M1 Pro with Metal.

Measured CPU-only on the real payloads (preamble 12,023 B, bundle 12,005 B,
validation 2,419 B) at `num_ctx=32768`, with `num_gpu=0` confirmed through
`ollama ps` reporting 100% CPU — one happy-path run of catalog, resolve and
validate:

| model | one run | slowest turn |
| --- | --- | --- |
| `llama3.2:3b` | 121.3 s | 8,006 tok prefill in 74.5 s, generating at 3.5 tok/s |
| `qwen2.5:7b-instruct` | 314.3 s | 8,810 tok prefill in 167.8 s, generating at 0.8 tok/s |

Projected required-gate time for two local arms per question, one run each, no
retries and no expansion: at 12 questions, 49 minutes on `llama3.2:3b` and 126
minutes on `qwen2.5:7b-instruct`. Those projections are optimistic. CPU
inference is memory-bandwidth bound and the measurement host has roughly an
order of magnitude more bandwidth than a shared cloud vCPU, so a cloud runner
should be expected to be 2-4x slower — 12 questions is plausibly 1.6 to 3.2
hours of required gate.

Shrinking the context window does not buy any of it back. The same content
prefills the identical 2,353 tokens in 27.0 s, 20.0 s and 21.1 s at allocations
of 8k, 16k and 32k: prefill tracks tokens processed, not window allocated. The
window choice and the CI cost are independent (ADR 0007's release gate is
unaffected by window size).

That left two options, and both changed something worth stating out loud: a
self-hosted runner, or a smaller required gate.

## Decision

Split the gate.

- **Required per-PR CI scores a recorded run.** It runs the deterministic
  scorers against a committed, versioned trace, and discloses itself as exactly
  that — a scored recording, not a live model run. This is the mechanism `#25`
  already permitted for arm 3, now used for arms 1 and 2 as well.
- **The live local arms run on a schedule.** Arms 1 and 2 execute against real
  Ollama nightly or weekly, and commit their versions and disclosures the same
  way arm 3 commits its own.
- **The pinned model is `qwen2.5:7b`.**

`#25`'s acceptance text is amended to say this, rather than leaving "required
CI runs arms 1-2" in place while quietly meaning something smaller.

A self-hosted runner was rejected because a *required* gate on a machine this
project owns breaks the OSS contribution model — the gate is unrunnable on a
fork — and creates a permanent infrastructure-availability dependency Hyperset
would then own. Honestly disclosing that per-PR CI scores a recording is the
lesser cost.

The model follows from the cost profile rather than driving it. `qwen2.5:7b` is
affordable once the live arms are off the per-PR path, and it is the model with
the worse bytes-per-token ratio of the two candidates (3.02 against 3.32), so
`CONSERVATIVE_BYTES_PER_TOKEN` is now the pinned model's own measured ratio and
not a hedge across candidates.

## Consequences

- Per-PR CI proves the harness, the scorers and the recorded trace's scores.
  It does not prove that a live model still passes. That gap is real and is
  disclosed rather than closed; the scheduled job is what closes it, later.
- A recorded trace is a release artifact with the same pinning obligations as
  any run: Ollama version, model tag and digest, quantization, prompt, tool
  schemas, seed, temperature and observed context window travel with it, or it
  is not evidence.
- A scheduled failure does not block a merge by itself. Acting on one is a
  human decision, which is the price of not owning a runner.
- `#36`'s release gate and ADR 0007's deterministic graders are unchanged in
  what they check. Only where and when arms 1 and 2 run against a live model
  changes.
