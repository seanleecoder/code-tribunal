# Deterministic Consensus

This document explains how Code Tribunal turns the raw, differently-shaped output
of independent LLM reviewers into a single, reproducible merge decision.

The governing principle is: **LLMs propose, deterministic Python decides.**

> No LLM call may decide the final surfaced set. The consensus engine
> ([`src/ai_review/consensus.py`](src/ai_review/consensus.py)) makes **no network
> calls and no model calls** — given the same input artifacts it always produces a
> byte-identical `consensus.json`.

Models only ever emit *candidate* findings and critiques. A pure function reduces
those candidates to `surfaced` / `fyi` / `drop` decisions and a `block_merge`
boolean.

---

## 1. Heterogeneous model output is funneled into one canonical schema

Three different CLIs (Claude Code, Codex, OpenCode) emit three different output
shapes. All of that is normalized **before** the consensus engine ever runs, so
the decision logic only sees clean, schema-valid data. There is deliberately **no
regex-scraping of prose** for findings — the funnel is:

1. **Prompt contract + JSON mode.** Each reviewer is instructed to emit a single
   strict-JSON object. The OpenRouter HTTP path
   ([`openrouter_reviewer.py`](src/ai_review/openrouter_reviewer.py)) additionally
   sets `response_format: json_object` and `temperature: 0.0`.

2. **Tolerant extraction** ([`adapter_runner.py`](src/ai_review/adapter_runner.py)).
   Strips markdown code fences, uses `JSONDecoder().raw_decode()` to find the first
   valid JSON value embedded in prose, parses streaming/JSONL event formats
   (Claude Code / OpenCode emit event streams rather than one flat object), and
   unwraps Claude's `{"result": "…"}` envelope.

3. **Normalization** (`schema.py:finalize_finding_batch`). Whitelists known keys
   (extra fields are dropped), and — critically — **recomputes each finding's
   `context_hash` from the actual MR diff** (`anchors.py`) rather than trusting the
   hash the model reported. Fingerprints and IDs are computed deterministically. A
   single malformed finding is dropped on its own, not fatal to the batch. Output
   is capped at `max_findings`, but sorted by severity/confidence first so blockers
   survive a verbose or prompt-injected flood.

4. **Hard JSON-Schema validation** ([`schemas/`](schemas/)). Closed enums for
   `severity` (`info|minor|major|blocker`) and `category`; every id must be a
   64-char SHA-256 hex string. If a reviewer's output fails validation it is written
   as an empty batch with `adapter_status: "schema_error"` and that reviewer simply
   drops out of the panel — nothing crashes.

Two anti-tamper details worth calling out: the model's self-reported `context_hash`
and its critic identity are **ignored and recomputed/reassigned by trusted code**
(the critic identity is bound from the output *filename*, not the payload). This is
what makes the pipeline resistant to prompt injection and to model
non-determinism.

---

## 2. What makes it deterministic

Four design choices:

- **Canonical JSON + SHA-256 for every id.** `canonical.py` sorts object keys,
  rejects non-finite numbers and duplicate keys; ids are
  `sha256_hex(canonical_json(...))`.
- **Exhaustive sorting** before any hash or emit.
- **Connected-components clustering (union-find), not order-dependent greedy
  grouping** — so the grouping result is independent of reviewer/input order.
- **No timestamps inside decision objects.**

Same inputs → identical `consensus.json`, every time.

---

## 3. The algorithm (`build_consensus()`)

1. **Panel status.** `failed` (0 successful reviewers) → `advisory_only`
   (< `panel.min_successful_reviewers_for_blocking`) → `degraded` (< enabled) →
   `full`. A `failed` panel short-circuits and the CLI exits `3`.

2. **Deduplication via union-find.** `same_issue(a, b)` is a symmetric predicate:
   same `source_finding_id` / validated critique duplicate-link; OR same
   path + category + side + `context_hash`; OR same path + category with overlapping
   line ranges and a matching title/evidence fingerprint or symbol. `group_findings`
   runs union-find over all pairs, then post-splits each component by
   (category, path) to prevent accidental cross-category merges. Because it is
   connected components rather than sequential merging, the result does not depend on
   input order.

3. **Voting & severity.** `vote_count` = number of **distinct reviewers** in a group
   (one vote per reviewer). `final_severity` = the **max** severity in the group.
   `votes_required` comes from `panel.quorum.votes_required` (default 2).

4. **Decision policy** (deterministic):
   - `advisory_only` panel → `fyi` (a lone security/correctness blocker may still
     `surface` as non-blocking).
   - `vote_count >= votes_required` → **`surface`**, with
     `block_merge = (severity == "blocker" AND severity_policy.quorum_blocker.block_merge)`.
   - A single-reviewer blocker in a configured category (`security`, `correctness`)
     → `surface`, `block_merge = false`, `human_ack_recommended = true`.
   - Otherwise → `fyi`.

5. **Critique-round adjustments** (`_apply_critiques`, only when critique is
   enabled — see below). Only `success`-status critique batches count, and a critic
   **cannot critique its own finding**. Majority non-author `noise` verdicts drop a
   group; a `duplicate` verdict only merges findings when a *third-party* critic's
   link is validated; severity downgrade and advisory escalation are both opt-in and
   the downgrade is capped at **one level**.

   The load-bearing invariant: **critiques can never push `vote_count` across
   quorum.** `agree` is confidence metadata only — never a vote. (Locked in by
   `test_agree_support_does_not_increase_vote_count`.)

6. **Finalization.** Every array is sorted, ids are canonical hashes, and the run's
   `summary.block_merge = any(group.block_merge)`. The `gate` stage
   ([`gate.py`](src/ai_review/gate.py)) then reads `summary.block_merge` to pass or
   fail the CI job.

---

## 4. When is the critique round applied?

Critique affects consensus only when **both** are true:

- `critique.enabled` is `true` **and** `critique.rounds == 1` in the effective config
  (checked in `adapter_runner.py` and `consensus.py:_critique_enabled`), and
- the critique jobs actually ran in CI.

These two layers are kept in lock-step by the **same** `AI_REVIEW_CRITIQUE_ENABLED`
variable: the CI `.critique_template` rule creates the jobs iff the variable is
exactly `"true"`, and `apply_env_overrides` sets `critique.enabled` from the same
variable using the same strict `true`/`false` semantics. The CI template sets it to
`"true"` by default (matching `review.yaml`), so the variable is always present in
CI and the two layers cannot disagree. (Locally there are no separate critique jobs,
so `review.yaml`'s value simply acts as the default when the variable is unset.) See
[README.md → Runtime Environment Overrides](../README.md#runtime-environment-overrides).

---

## 5. Tests that pin this behavior

Under [`tests/unit/`](tests/unit/): `test_voting.py` (panel status + decision
policy), `test_grouping.py` (union-find), `test_phase5_consensus.py` (critique
merges, majority-noise drop, `agree` doesn't add a vote, opt-in escalation/downgrade,
`rounds: 0` ignores critiques), `test_consensus_state_matching.py` (cross-run
matching + deterministic tie-breaking), and `test_consensus_cli.py` (failed panel
still writes an artifact and exits `3`; critic identity is bound from the filename
even if the payload lies).
