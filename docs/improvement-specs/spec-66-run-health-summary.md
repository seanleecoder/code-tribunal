# SPEC-66 — Run health in the summary comment

- **Severity:** Medium (maintainers cannot tell from the merge request whether a review ran completely, what it ran with, or whether to re-run it) · **Effort:** L, in three stageable pull requests (see [Exact implementation surface](#exact-implementation-surface)) · **ROI rank:** post-2.0.2, first
- **Depends on:** no other spec.
- **Vehicle (decided 2026-10-13):** compatible minor releases, with optional fields on the existing artifact versions. The stages ship in order (a), (b), (c). See [Versioning](#versioning).
- **Absorbs:** SPEC-60 (critique-quality observability). Its group-coverage and critique-effect counts are specified in [Critique coverage](#critique-coverage-absorbed-from-spec-60) so that critique observability does not take a second consensus version step.
- **Related:** [S06](simplification-audit/SIMPLIFICATION_REVIEW.md) (one result envelope) proposes the opposite artifact direction for status files; see [Versioning](#versioning).

## Rationale

In 2.0.2 the merge request carries no health information at all:

- The summary comment lists only fallback and FYI findings. It is not created
  when there are none.
- A degraded panel looks the same as a full one.
- A failed panel publishes nothing.
  - Consensus writes its artifact locally and exits 3.
  - On GitHub, the failed step skips the upload, and post stops at its
    "Require successful consensus" step.
  - On GitLab, the artifact is kept (`when: always`), but post is never
    scheduled after a failed consensus job.

A maintainer who sees no findings cannot tell any of these apart: a clean
change, a panel that lost two seats, or a run that never published. The
merge request also doesn't show what models and efforts ran, how long each job
took, what the run cost, whether critique ran, or whether to re-run anything.

Most of that information already exists but never reaches the comment:

- The trusted runner writes an `adapter_status.v1` file for every review and
  critique job. It records outcome, `duration_ms`, `error_class` and a redacted
  error message, but no stage after the adapter reads it.
- Model, effort and timeouts live in the manifest's `effective_config`, which
  consensus already verifies against its live configuration digest.
- Per-seat raw, accepted and dropped counts are in every `finding_batch.v1`,
  and the cap-omitted count is derivable from them. `consensus.v2` aggregates
  none of them. Those dropped counts are exactly how the
  [SPEC-41](spec-41-reviewer-confidence-default.md) loss would become visible.
- Every `finding_batch.v1` also carries `model`, `started_at` and
  `completed_at`, so review model and wall-clock duration are available from
  the batches alone, without status files.

Several things are missing entirely:

- **Critique-stage health.** `panel_status` only looks at review seats. If every
  critic times out, the panel still reports `full`, even though no finding could
  gain critique support.
- **The "unable to surface anything" case.** A finding surfaces only when two
  reviewer identities support it across review and critique. A critic whose own
  review failed still counts (see
  `test_critique_batch_counts_even_when_its_review_batch_failed`). With one
  usable review seat and no successful critic from another identity, nothing
  can surface, yet the panel reports only `degraded`.
- **Jobs killed by the CI ceiling.** These jobs write no status file, so the seat
  appears in `failed_reviewers` with no reason.
- **"Ran, but not fully".** A terminal CLI error after findings were emitted
  (for example `error_max_turns`) keeps the batch as `success`. This is not
  logged and not recorded anywhere.
- **Token usage and cost.** Nothing is captured. Codex discards its stdout entirely.

## Decisions

1. **One summary section, not a new thread.** Health renders as the first
   section of the existing summary comment. A thread is reserved for surfaced findings under
   [ADR-0003](../decisions/0003-product-invariants-and-complexity-envelope.md).
   A health thread would be a new public artifact family, and resolving it
   would mean nothing to thread reconciliation. No ADR is required for this
   spec: it extends existing artifact families (`adapter_status`, `consensus`)
   and the existing summary comment.
2. **Consensus computes, post renders.** The health record is a deterministic
   part of the consensus artifact, built by consensus from artifacts it is
   given. Post and `summary_render` only read it. Neither stage calls a CI or
   platform API to learn job status. This follows the dependency direction in
   [`AGENTS.md`](../../AGENTS.md).
3. **Observational only.**
   - Health never changes a group decision, `panel_status`, `failed_reviewers`,
     resolution eligibility, or any exit code.
   - A missing, malformed or contradictory status file degrades only the
     health record. It never fails consensus or post.
   - Finding batches stay the sole authority for whether a seat is usable.
4. **No price table.** Cost appears only when the pinned CLI itself reports it,
   and it is labelled as a CLI-reported estimate. Seats that don't report it
   show `n/a`. Code Tribunal does not maintain per-model prices.
5. **Coarse re-run advice.** The only re-run action is "re-run the review from
   prepare". This matches the existing guidance in
   [operations](../operations.md) and [troubleshooting](../TROUBLESHOOTING.md):
   do not mix artifacts across runs. Per-job retry advice is out of scope until
   [the open question](#open-questions) is answered.
6. **Always present.** The summary comment is created on every run, including a
   full-health run with no findings and a failed panel. Edits in place update
   it, and identical inputs produce identical bytes.

## Scope

**In:**

- `adapter_status` usage and completeness notes;
- per-CLI usage extraction;
- consensus reading status files;
- the `run_health` consensus record and its grade/action derivation;
- CI template arguments;
- summary rendering and job-log output;
- failed-panel publication;
- schema, type, golden and test updates.

- critique coverage and effect counts (absorbed from SPEC-60).

**Out:**

- any critique-quality *policy*: a critique-required threshold, or a decision
  that depends on coverage;
- per-job retry automation;
- any new retry, quorum or gate policy;
- CI-API job inspection;
- price tables;
- inline thread bodies;
- the `render-body` version;
- config keys.

## Adapter execution: optional fields on `adapter_status.v1`

Add two optional properties to the existing `adapter_status.v1` schema, keeping its
identifier, for the status artifact built in
[`schema.py`](../../ai-review/src/ai_review/schema.py) and written by
[`adapter_artifacts.py`](../../ai-review/src/ai_review/adapter_artifacts.py).

```json
{
  "usage": {
    "input_tokens": 412003,
    "output_tokens": 9120,
    "cache_read_tokens": 380112,
    "cost_usd": 0.6132
  },
  "notes": ["terminal_error_after_output"]
}
```

**`usage`** is either an object or `null`.

- Every member is optional. Token counts are integers between 0 and 10^10.
  `cost_usd` is a number between 0 and 10^4.
- Write only fields the CLI reported. Never compute or estimate a field.
- `usage` is `null` on timeout, on any non-`success` outcome, and when the CLI
  reported nothing.

**`notes`** is a sorted, de-duplicated array drawn from a closed enum. It records
completeness facts that are currently silent or log-only:

| Note | Set when |
|---|---|
| `terminal_error_after_output` | The CLI reported a terminal error (`is_error`/error subtype) but text or structured output was still parsed (the silent path in [`adapter_output.py`](../../ai-review/src/ai_review/adapter_output.py) `_load_stream_json`). |
| `structured_output_unused` | The schema-steered `structured_output` was absent and the result text was parsed instead. |
| `nonzero_exit_with_output` | The adapter exited non-zero but stdout was non-empty, so parsing proceeded. |

Model and effort are **not** added: consensus reads them from the verified
effective configuration.

### Per-CLI usage capture

Each source below must be confirmed against a fixture captured from the pinned
CLI version before its extractor lands. A field that is absent on the pinned
version yields `null` rather than a guess. Field names here are the documented
or expected shapes, not repository facts.

| Seat | Source | Fields mapped | Cost |
|---|---|---|---|
| Claude | The `result` event already read by `_load_stream_json`: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`, `total_cost_usd`. | All four | CLI-reported. Computed by the CLI from list prices, so it may differ from what OpenRouter bills. |
| Codex | Add `--json` to `codex exec` in [`codex.sh`](../../ai-review/adapters/codex.sh) and redirect the event stream (currently `>/dev/null`) to `out/.tmp/<reviewer>-<stage>.events.jsonl`. The `-o` last-message file remains the answer source. The runner sums `usage` from `turn.completed` events after exit. | Tokens | `n/a` |
| OpenCode | The `info` object of the session message response in [`opencode_client.py`](../../ai-review/src/ai_review/opencode_client.py): `tokens.input`, `tokens.output`, `tokens.cache.read`, `cost`. The client adds `usage` to the normalized envelope it already prints. | All four | CLI-reported, from OpenCode's own price data |
| Cursor | Any `usage` member of the `--output-format json` result envelope. | Tokens if present | Never. Subscription-billed. |

Usage numbers come from CLI output in a job that also runs model tools.
Validate them as bounded numbers and treat them as untrusted (see
[Security](#security)).

## Consensus: the `run_health` record

### Inputs

Add `--status-dir` to the consensus CLI, defaulting to `out/status`. Consensus
reads `<reviewer>.json` and `critique-<reviewer>.json` for every enabled seat
and ignores every other file, including debug files. Wire it in both templates:

- GitHub: `--status-dir reviewer-artifacts/status`. The review and critique
  artifacts already deliver `status/` there.
- GitLab: `--status-dir out/status`. The job's `needs` already deliver it.

Update the installed GitHub workflow copy through the parity sync.

A status file is **usable** only if all of these hold:

- it validates against `adapter_status.schema.json`;
- its `reviewer` and `stage` match its filename;
- its `run_id` equals the manifest's.

Otherwise, treat it as absent and add `status_unreadable` to that job's notes.

### Per-job outcome

For each enabled seat and stage (review always; critique only when
`critique.enabled`), derive exactly one `outcome`. For each condition below,
review and critique are checked in the same order, top to bottom, and the first
match wins:

| Condition | Review outcome | Critique outcome |
|---|---|---|
| A batch exists and is usable (`batch_usable_for_panel`, or a successful critique batch) | `success` | `success` |
| A batch exists, its status is `success`, and it is not usable | `unusable` (every finding rejected) | n/a |
| A non-success batch exists | its `adapter_status` (`timeout`, `model_error`, `schema_error`, `config_error`, `internal_error`) | same |
| No batch exists; a usable status file reports a non-success status | that status | same |
| No batch exists; a usable status file reports `success` | `batch_missing` | `batch_missing` |
| Neither a batch nor a usable status file exists | `missing` | `missing` |

Only a usable batch yields `success`. A status file never promotes a job; it
adds telemetry (`duration_ms`, `usage`, `error_class`) to whatever outcome the
batches determine. This keeps `run_health` consistent with `failed_reviewers`:
every review job that isn't `success` is a seat the reducer counts as failed.

The batch's `adapter_status` wins over the status file's `status` if they
disagree. Record `status_disagrees_with_batch` in notes when that happens.

### Record shape

`run_health` is a new **optional** top-level property of `consensus.v2`. The schema
identifier does not change (see [Versioning](#versioning)). Schema optionality exists
only for compatibility; it does not make the field optional in practice:

- **Producer:** consensus from this release on always writes `run_health`.
- **Consumer:** post requires it. An artifact without `run_health` comes from an
  earlier release, so post rejects it before any platform mutation, with a message
  saying it is from an earlier release. There is no fallback rendering, so no dual
  path and no temporary-compatibility row.

The record looks like this:

```json
"run_health": {
  "grade": "degraded",
  "recommended_action": "optional_rerun",
  "reasons": ["review_seat_unavailable"],
  "jobs": [
    {
      "stage": "review",
      "reviewer": "opencode",
      "outcome": "timeout",
      "model": "xiaomi/mimo-v2.6-flash",
      "effort": null,
      "timeout_seconds": 1800,
      "duration_ms": 1795012,
      "raw_finding_count": null,
      "accepted_finding_count": null,
      "dropped_finding_count": null,
      "cap_omitted_finding_count": null,
      "critique_count": null,
      "usage": null,
      "notes": ["near_timeout"],
      "error_class": "TimeoutExpired",
      "error_message": null
    }
  ]
}
```

Field rules:

- **`model`, `effort`, `timeout_seconds`** come from the live effective
  configuration that consensus already digest-checks. `timeout_seconds` is the
  stage-specific value. `effort` is `null` when unset.
- **Finding counts** are set only for a review job with a batch.
  `cap_omitted_finding_count` is per seat: raw − accepted − dropped.
- **`critique_count`** is the number of critiques, set only for a successful
  critique job.
- **`duration_ms`, `usage`, `error_class`** come from the status file, or are
  `null` without one.
- **`error_message`** comes from `error_message_redacted`, normalized to one
  line and truncated to 300 characters by a renderer-independent rule.
- **`notes`** is the status-file notes plus the consensus-derived
  `near_timeout`, `status_unreadable` and `status_disagrees_with_batch`. It is
  sorted. `near_timeout` means `duration_ms ≥ 0.8 × timeout_seconds × 1000`.
- **`jobs` order:** review before critique, then by reviewer ID. Disabled seats
  are omitted.

### Grade and action

The derivation is one pure function beside `panel_status` in
[`consensus.py`](../../ai-review/src/ai_review/consensus.py). It reads only
the consensus fields and `jobs`. `panel_status` keeps its meaning and its
exit-code role; `grade` is a superset view that never feeds back into it.

**`grade`:**

1. `rerun_recommended` if any of these hold:
   - `panel_status == "failed"`;
   - **two-identity support is unreachable:** at least one review seat
     succeeded, but the union of reviewers with a usable review batch and
     critics with a successful critique batch has fewer than two identities;
   - critique is enabled and no critique job succeeded. This is coverage
     policy, not reachability: with two or more usable review seats, support
     is still reachable through review overlap, but the critique half of the
     pipeline is lost.
2. Otherwise `degraded` if any of these hold:
   - a review or critique job is not `success`, including the case where
     fewer than two review seats succeeded but critique keeps support
     reachable (reason `review_coverage_below_two`);
   - any dropped or cap-omitted count is non-zero;
   - any job carries `terminal_error_after_output` or `status_unreadable`.
3. Otherwise `full`.

`near_timeout`, `structured_output_unused`, `nonzero_exit_with_output` and
`status_disagrees_with_batch` are shown but never lower the grade.

**`recommended_action`** (first match wins):

1. `fix_configuration` if any job's outcome is `config_error`, or its
   `error_class` is `ReviewerConfigValidation`. Re-running will not help.
2. `rerun_from_prepare` if the grade is `rerun_recommended`.
3. `optional_rerun` if the grade is `degraded` and some job is not `success`.
4. `none` otherwise. This covers full health, and degraded health caused only
   by drops, caps or completeness notes.

**`reasons`** is the sorted set of closed-enum codes that fired:

- `panel_failed`
- `support_unreachable`
- `review_coverage_below_two`
- `all_critics_unavailable`
- `review_seat_unavailable`
- `critic_unavailable`
- `findings_dropped`
- `findings_cap_omitted`
- `seat_output_incomplete`
- `status_unreadable`
- `configuration_error`

Consensus also prints a plain-text rendering of `run_health` to stdout so the
job log carries it on both platforms.

### Critique coverage (absorbed from SPEC-60)

SPEC-60 was drafted on 2026-08-18 but never merged (branch `spec-60`). Its
intent is kept here so critique observability shares this record and its
single version step. Execution health above says whether critics *ran*; this
subsection says whether critique *reached* the groups and what it *changed*.
Both are deterministic projections of fields consensus already has.

Add to `run_health`:

```json
"critique": {
  "status": "full",
  "groups_full_coverage": 7,
  "groups_partial_coverage": 1,
  "groups_no_coverage": 2,
  "groups_not_applicable": 0,
  "groups_surfaced_by_critique_support": 3,
  "groups_demoted_by_noise_majority": 1,
  "verdicts_by_critic": [
    {"critic": "claude", "agree": 9, "dispute": 2, "noise": 1, "duplicate": 3}
  ]
}
```

- **`status`**: `disabled` when `critique.enabled` is false; otherwise `failed`,
  `degraded` or `full` as zero, some or all enabled critique jobs have
  `outcome == "success"`. A successful empty critique batch is a success.
- **Group coverage**: for each group, the eligible critics are successful
  critics that are not in `contributing_reviewers` (the existing
  direct-contributor exclusion in `critique.py`). A critic is observed when its
  collapsed effective verdict covers at least one source finding in the group.
  A valid `duplicate` counts; an invalid one does not. `full` means every
  eligible critic was observed and at least one exists. `partial` means some were
  observed. `none` means eligible critics exist but none was observed.
  `not_applicable` means no eligible critic exists. Only counts are recorded,
  not per-group objects.
- **`groups_surfaced_by_critique_support`**: groups with `decision == "surface"`
  that would not have reached two identities from `contributing_reviewers`
  alone.
- **`groups_demoted_by_noise_majority`**: groups demoted to FYI by the
  majority-noise rule ([SPEC-67](spec-67-majority-noise-to-fyi.md)). These are
  `fyi` groups with `support_count >= 2` that are not ambiguous.
- **`verdicts_by_critic`**: per successful critic, counts of collapsed effective
  verdicts. It is sorted by critic ID.

Rendering: one line in the collapsed run details, for example
`Critique: full · 7 groups fully covered, 1 partial, 2 uncovered · 3 surfaced
through critique support · 1 demoted as noise`. It never appears in a thread
body.

Grade effect: none beyond the `critique` job outcomes already counted above.
Coverage never changes a decision, severity, routing or exit code. Any later
policy built on this data needs its own ADR or spec. Before proposing one,
review it by provider:

- how often runs are full, degraded or failed;
- how many surfaced groups have no independent critique coverage;
- whether one provider systematically fails critique while succeeding at review.

## Rendering

[`summary_render.py`](../../ai-review/src/ai_review/summary_render.py) gains
`run_health_section(consensus)`, which renders first, directly after the
summary title. The section has three parts, in this order:

1. **Headline** (always visible). Grade, the counts of completed review and
   critique jobs, and the panel-wide raw, accepted, dropped and cap-omitted finding counts summed from `jobs`.
2. **Problems and action** (visible, and only when the grade is not `full`).
   - One bullet per non-`success` job, giving the stage, reviewer, outcome
     wording, duration against the limit, and the truncated error message.
   - Then one action line.
3. **Run details** (collapsed `<details>`). A table with one row per job, and a
   summary line giving longest review duration, longest critique duration, and
   total reported cost.

Error messages are rendered as list items below the table, never inside table
cells.

````markdown
**Review health: degraded — re-run optional.** 2 of 3 review seats and 3 of 3 critics completed. Findings: 41 raw, 37 accepted, 4 dropped, 0 omitted by the per-seat cap.

- `opencode` review: timed out after 29m55s (limit 30m00s).

Action: none required; findings from the remaining seats were evaluated. Re-run the review from prepare if `opencode` coverage matters for this change.

<details>
<summary>Run details · longest review 29m55s · longest critique 6m10s · $0.84 reported by 2 of 3 seats (CLI estimates)</summary>

| Stage | Reviewer | Model | Effort | Outcome | Time | Findings raw/acc/drop/cap | Tokens in/out | Cost |
|---|---|---|---|---|---|---|---|---|
| review | `claude` | `anthropic/claude-haiku-4.5` | medium | success | 12m04s | 18/16/2/0 | 412k/9.1k | $0.61 |
| review | `codex` | `openai/gpt-6-luna` | low | success | 8m41s | 23/21/2/0 | 198k/6.4k | n/a |
| review | `opencode` | `xiaomi/mimo-v2.6-flash` | default | timeout ⚠ near limit | 29m55s | — | n/a | n/a |
| critique | `claude` | … | … | success | 4m02s | 12 critiques | … | $0.19 |

</details>
````

Fixed wording:

**Headline**, selected by `recommended_action` first and grade second, so it
never contradicts the action line:

| Action | Grade | Headline |
|---|---|---|
| `fix_configuration` | any | `Review health: configuration error — fix before re-running.` |
| `rerun_from_prepare` | `rerun_recommended` | `Review health: re-run recommended.` |
| `optional_rerun` | `degraded` | `Review health: degraded — re-run optional.` |
| `none` | `degraded` | `Review health: degraded.` |
| `none` | `full` | `Review health: full.` |

**Action lines:**

| Action | Text |
|---|---|
| `fix_configuration` | Fix the reviewer configuration named above; re-running unchanged will fail the same way. |
| `rerun_from_prepare` | Re-run the review from prepare; do not retry individual jobs or mix artifacts. |
| `optional_rerun` | The sentence in the example above. |
| `none` | No action line. |

**Outcome wording:**

| Outcome | Text |
|---|---|
| `timeout` | timed out after X (limit Y) |
| `model_error` | provider or model error |
| `schema_error` | output could not be parsed |
| `unusable` | every reported finding was rejected during normalization |
| `batch_missing` | the job reported success but its batch was not delivered to consensus |
| `config_error` | configuration error |
| `internal_error` | internal error |
| `missing` | no status recorded; the job was likely cancelled or killed by the CI job ceiling |

**Formatting:**

- Durations use `XmYYs`.
- Tokens use `k`/`M`, rounded half-even to one decimal.
- Costs use `$` with two decimals, summed only over seats that reported a cost.
- `n/a` means not reported. `—` means not applicable.

**Size:**

- The headline, problem bullets and action are reserved space. The existing
  drop loop truncates finding sections only.
- Keep the layout open for persisted state. The next major release moves state
  inside this comment (simplification item N04: one bot comment per merge request).
  Size the reserved health space so that state can later claim its own reserved
  block within the platform limit. Do not add a second always-present comment.
- The details block is bounded: at most eight rows, plus error text capped at
  300 characters per job.
- It is part of the reserved section, not a droppable section descriptor.

**Posting:**

- In [`posting.py`](../../ai-review/src/ai_review/posting.py),
  `upsert_summary_comment` no longer returns early when there are no fallback
  or FYI findings. The health section is always non-empty.
- When a later run recovers, the next upsert replaces the old body with the new
  health section. No separate recovery wording is needed.

### Failed-panel publication

Today a failed panel publishes nothing. This spec makes post publish a
health-only summary and then preserve the failure:

- **Consensus CLI:** unchanged. It writes the artifact before returning 3.
- **GitHub consensus job.** Today the "Build consensus" step has no
  `continue-on-error`, so exit 3 skips the upload step that follows it. Change
  the canonical template and its synchronized copy so that:
  - "Build consensus" gets an `id` and `continue-on-error: true`;
  - the upload step runs when `steps.consensus.outcome` is `success` or
    `failure`, and keeps `if-no-files-found: error`;
  - a new final step, "Preserve consensus failure", fails the job when
    `steps.consensus.outcome != 'success'`, so `needs.consensus.result` is
    still `failure`.
- **GitHub post job.** Replace "Require successful consensus" with a gate that:
  - passes when `needs.consensus.result == 'success'`;
  - when consensus failed, downloads the artifact and continues only if it
    validates with `panel_status == "failed"`. A missing or invalid artifact
    (including consensus integrity exit 3, which writes none) fails before any
    mutation;
  - after post publishes a failed-panel summary, fails the job in a final step.
- **GitLab:** let only consensus exit code 3 through to post, using
  `allow_failure: exit_codes: 3` plus post scheduling that tolerates it. Post
  restores exit 3 after a successful publication. Other exit codes still stop
  post, and the manual-prepare dependency keeps default scheduling. Consensus
  integrity errors also exit 3 but write no artifact, so post fails on the
  missing artifact before any mutation.
- **What gets published:** only the summary comment, with no groups. No thread
  is created, resolved or reconciled, and persisted state is not rewritten.
  Existing threads are left untouched.

This repeats the failed-panel path from the reverted S05 implementation
(#171, `5845db9`, reverted in `63ca67a` for 2.0.2). Its live GitLab scheduling lesson still applies:
a plain `when: always` post started before prepare had run.

## Security

- **Status files are untrusted input to consensus.** They are written by the
  trusted runner, but in a job that also runs model CLIs with tool access.
  - Schema validation, the run-ID binding and numeric bounds apply.
  - A forged status file can mislead only the health panel; batches remain the
    decision input.
  - This must be stated in the [security model](../SECURITY_MODEL.md).
- **All text renders literally.** Every model ID, reviewer ID and error message
  goes through `literal_span`.
- **Error text is redacted, then truncated.** `error_message` passes through
  the existing redaction before truncation.
- **No sensitive data in the record.** It holds no prompts, model bodies,
  endpoints, credentials or provider session IDs.

## Versioning

2.0.2 ships `consensus.v2` and `adapter_status.v1`, and both schemas are
closed (`additionalProperties: false`). On 2026-10-13 the maintainer chose
Option 1. Option 2 is kept below as the rejected alternative.

- **Option 1 — compatible minor release (chosen).**
  - `run_health` is an *optional* property of `consensus.v2`.
  - `usage` and `notes` are optional on `adapter_status.v1`.
  - `artifacts-and-schemas.md` documents that same-identifier optional
    widening is a minor change.
  - This is sound because producer and consumer always run from one pinned
    image pair, and mixing artifacts across runs is already unsupported. 1.0.1
    set the precedent by tightening the `consensus.v2` category enum in place.
  - It closes the 2.0.2 "no posted loss notice" gap (SPEC-41 part B) without
    waiting for S05, which has no plan.
  - The always-present summary and the failed-panel publication are additive
    behaviour, so they belong in a minor release.
- **Option 2 — next major release (rejected).** Adding the fields is treated as breaking.
  The spec shares one version step with S05: `consensus.v3` and
  `adapter_status.v2`.

**Status files versus S06.** S06 proposes merging batches and status mirrors
into one result envelope, while this spec makes status files a consensus input.
Option 1 resolves this conflict:

- *Option 1 (chosen):* keep status files as an input, as specified above. If
  S06 lands in the next major release, it moves `duration_ms`, `usage` and
  `notes` into the batch envelope and deletes the status-dir loader.
- *Option 2 bundled with S05:* put `duration_ms`, `usage` and `notes` in the
  batch envelopes instead. That deletes the `--status-dir` loader, its binding
  rules and the `status_unreadable` and `status_disagrees_with_batch` cases.
  Only `batch_missing` (derived from the enabled roster) and `missing` remain.

**Either way:**
  - no old-version decoders and no dual paths, so no temporary-compatibility
    row;
  - the summary marker stays `ai-review-summary:v1`;
  - the body hash changes, so the next run refreshes each existing summary
    once;
  - `render-body` is untouched.

## Exact implementation surface

Split this into three pull requests, one per boundary. They ship in successive
minor releases, in this order, because each is useful alone:

- **(a)** consensus `run_health` from batches, with critique coverage, rendering
  and the always-present summary. This is effort M and delivers the SPEC-41 loss
  notice.
- **(b)** usage capture. This is effort M; the Codex `--json` sidecar is the
  riskiest part.
- **(c)** failed-panel publication. This is effort M and needs live evidence on
  both platforms.

The numbered list below is grouped by boundary.

1. **Adapter execution.**
   - Optional `usage` and `notes` properties on `adapter_status.schema.json`,
     keeping `adapter_status.v1`, plus types and `schema.py` builder fields.
   - Document in [artifacts and schemas](../reference/artifacts-and-schemas.md)
     that adding an optional property under the same identifier is a minor
     change.
   - Usage and notes extraction in `adapter_output.py`, `opencode_client.py`
     and `adapter_runner.py`.
   - The `codex.sh` `--json` sidecar.
   - Captured pinned-version fixtures.
   - Fix the review-stage debug filename mismatch in
     [artifacts and schemas](../reference/artifacts-and-schemas.md) while
     touching it.
2. **Consensus.**
   - The `--status-dir` loader and validation, the per-job derivation, the
     grade/action function, and stdout rendering.
   - An optional `run_health` property on `consensus.schema.json`, keeping
     `consensus.v2`, plus types and goldens.
   - Both CI templates, plus the GitHub parity copy.
3. **Posting/rendering.**
   - `run_health_section`.
   - Unconditional summary creation.
   - Failed-panel publication in post and both templates, plus the GitHub
     parity copy.
   - Update the [configuration](../configuration.md) and
     [operations](../operations.md) docs on reading the panel, plus
     [CHANGELOG](../../CHANGELOG.md).

## Acceptance criteria

- Every enabled review seat appears in `run_health.jobs`, and so does every
  critique seat when critique is enabled. Each carries model, effort, timeout,
  outcome, duration and reported usage, and no field is invented.
- A job killed without writing artifacts appears as `missing`, with the
  CI-ceiling wording.
- A panel where every critic failed grades `rerun_recommended`.
- With one usable review seat out of three:
  - and no successful critic from another identity, the panel grades
    `rerun_recommended` (`support_unreachable`) while `panel_status` stays
    `degraded`;
  - and a successful critique from another identity, it grades `degraded`
    (`review_coverage_below_two`), matching the reducer's ability to surface
    through critique agreement.
- A missing batch whose status file reports `success` grades the job
  `batch_missing`, never `success`.
- Removing or corrupting every status file leaves group decisions,
  `panel_status`, `failed_reviewers` and exit codes byte-identical. Only
  `run_health` changes.
- A `config_error` seat yields `fix_configuration`, and neither the headline
  nor the action line suggests a re-run, even when the grade is
  `rerun_recommended`.
- A full run with no findings creates a summary comment whose only content is
  the health headline and the collapsed details.
- A failed panel publishes a health-only summary on both platforms. It leaves
  threads and state untouched and still ends the pipeline failed. A missing or
  invalid consensus artifact fails before any mutation.
- Repeating consensus and post with identical inputs yields identical artifacts
  and summary bytes.
- No CI or platform API call is added to consensus or post.

## Required tests

- `ai-review/tests/unit/test_adapter_runner.py`: usage extraction from captured
  Claude and Cursor fixtures; `terminal_error_after_output`,
  `structured_output_unused` and `nonzero_exit_with_output` notes; `usage: null`
  on timeout and on errors.
- `ai-review/tests/unit/test_openrouter_adapters.py`: the Codex `--json`
  sidecar, with the last message still the answer source; a missing sidecar
  yields `usage: null`.
- `ai-review/tests/unit/test_opencode_client.py`: `usage` in the normalized
  envelope.
- `ai-review/tests/unit/test_consensus_cli.py`: `--status-dir` loading; ignoring
  debug files; filename/run-ID mismatch becomes `status_unreadable`; a missing
  status directory still produces consensus.
- `ai-review/tests/unit/test_consensus_reducer.py`: one test per grade, action
  and reason rule, including all critics failed, one usable seat with and
  without a successful other-identity critic, a success status with no batch,
  a config error, a config error combined with an otherwise unreachable panel,
  and drops-only `none`; an invariance test that strips status files and
  asserts identical non-health output.
- `ai-review/tests/unit/test_panel_degradation.py`: grade derivation against the
  existing degraded/failed panel scenarios; critique coverage status for
  disabled, all-succeeded, one-failed, all-failed and successful-empty critics;
  direct contributors excluded from eligibility; valid versus invalid duplicate
  observation; and removing the `critique` object changes no decision.
- `ai-review/tests/unit/test_schema_validation.py` and
  `ai-review/tests/unit/test_types_schema_alignment.py`: the optional
  `usage`/`notes` on `adapter_status.v1` and the optional `run_health` on
  `consensus.v2`, with their shapes and bounds.
  - A 2.0.2-shaped status file without the new fields still validates.
  - A 2.0.2-shaped consensus artifact without `run_health` still validates
    against the schema.
  - `test_post.py` asserts that post rejects that artifact before any platform
    call.
- `ai-review/tests/unit/test_summary_render.py`: every row of the headline
  table, including `fix_configuration` on a `rerun_recommended` grade; hostile model IDs and error text render literally; error text never
  appears inside a table cell; the reserved section survives truncation at both
  platform limits.
- `ai-review/tests/unit/test_post.py`: a summary is created for a full run with
  no findings; recovery and idempotency; failed-panel posting is health-only
  and makes no thread or state mutation.
- `ai-review/tests/unit/test_ci_template.py`: both templates pass
  `--status-dir`; failed-panel scheduling on both platforms (GitHub uploads the
  consensus artifact after exit 3 and the consensus job still fails; only
  GitLab exit 3 passes through; post re-fails after publishing on both); the GitHub parity
  copy matches.
- `ai-review/tests/integration/test_publish_e2e.py`: a failed panel with a
  persisted open thread leaves that thread and its state record unchanged.
- `ai-review/tests/contract/test_golden_consensus.py`: goldens carry
  `run_health` and are stable under file-order permutation.

## Open questions

- **Per-job retry.** Can a single failed job be retried safely and still be
  accepted by consensus?
  - A retried review job reads the same manifest, so its `run_id` matches. Its
    configuration digest matches only if no `AI_REVIEW_*` variable changed.
  - On GitHub, it is unverified whether "re-run failed jobs" gives a later
    attempt access to earlier-attempt artifacts, and whether it re-drives
    consensus and post.
  - On GitLab, the [evidence runbook](../evidence/RUNBOOK.md) warns against
    pipeline retry.
  - Until both platforms are verified, the panel advises only "re-run from
    prepare".
- **Status upload after a timeout kill.** Whether `if: always()` and
  `when: always` uploads run after a platform timeout kill decides how often
  `missing` appears versus a real `timeout`. Collect one live run per platform
  as evidence. It doesn't change the design.
