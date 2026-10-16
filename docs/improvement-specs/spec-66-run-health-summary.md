# SPEC-66 — Run health in the summary comment

- **Severity:** Medium (maintainers cannot tell from the merge request whether a review ran completely, what it ran with, or whether to re-run it) · **Effort:** M, one pull request · **ROI rank:** post-2.0.2, first
- **Depends on:** no other spec.
- **Vehicle (decided 2026-10-13):** a compatible minor release (2.1.0), as an optional field on the existing `consensus.v2`. See [Versioning](#versioning).
- **Scope (narrowed 2026-10-16):** one stage. Usage and cost capture, failed-panel publication, critique-coverage counts (formerly SPEC-60), and the action/reason/notes enums were removed; see [Decision record](#decision-record).
- **Related:** [S06](simplification-audit/SIMPLIFICATION_REVIEW.md) (one result envelope) proposes the opposite artifact direction for status files; see [Versioning](#versioning).

## Rationale

In 2.0.2 the merge request carries no health information at all:

- The summary comment lists only fallback and FYI findings. It is not created
  when there are none (`upsert_summary_comment` returns early in
  [`posting.py`](../../ai-review/src/ai_review/posting.py)).
- A degraded panel looks the same as a full one.
- A failed panel turns the consensus check red on both platforms, but consensus
  exits 3 without logging why. Nothing in the job log names the failed seats.

A maintainer who sees no findings cannot tell a clean change from a panel that
lost two seats. The merge request also doesn't show what models and efforts
ran, how long each job took, or whether critique ran.

Most of that information already exists but never reaches the comment:

- The trusted runner writes an `adapter_status.v1` file for every review and
  critique job. It records outcome, `duration_ms`, `error_class` and a redacted
  error message, but no stage after the adapter reads it. It is the only source
  of `error_class`, of critique timings, and of the outcomes where the runner
  writes no batch (`_fail_status_only` in
  [`adapter_runner.py`](../../ai-review/src/ai_review/adapter_runner.py)).
- Model, effort and timeouts live in the manifest's `effective_config`, which
  consensus already verifies against its live configuration digest.
- Per-seat raw, accepted and dropped counts are in every `finding_batch.v1`,
  and the cap-omitted count is derivable from them. `consensus.v2` aggregates
  none of them. Those dropped counts are exactly how the
  [SPEC-41](spec-41-reviewer-confidence-default.md) loss becomes visible.

Two cases are missing entirely:

- **Critique-stage health.** `panel_status` only looks at review seats. If every
  critic times out, the panel still reports `full`, even though no finding could
  gain critique support.
- **The "unable to surface anything" case.** A finding surfaces only when two
  reviewer identities support it across review and critique. A critic whose own
  review failed still counts (see
  `test_critique_batch_counts_even_when_its_review_batch_failed`). With one
  usable review seat and no successful critic from another identity, nothing
  can surface, yet the panel reports only `degraded`.

## Decisions

1. **One summary section, not a new thread.** Health renders as the first
   section of the existing summary comment. A thread is reserved for surfaced
   findings under
   [ADR-0003](../decisions/0003-product-invariants-and-complexity-envelope.md).
   No ADR is required: this extends the existing `consensus` artifact and the
   existing summary comment.
2. **Consensus computes, post renders.** The health record is a deterministic
   part of the consensus artifact, built from artifacts consensus is given.
   Post and `summary_render` only read it. Neither stage calls a CI or platform
   API to learn job status.
3. **Observational only.**
   - Health never changes a group decision, `panel_status`, `failed_reviewers`,
     resolution eligibility, or any exit code.
   - A missing, malformed or contradictory status file degrades only the
     health record. It never fails consensus or post.
   - Finding batches stay the sole authority for whether a seat is usable.
4. **No usage or cost.** Claude, Codex and OpenCode bill through one
   `OPENROUTER_API_KEY`, whose spend OpenRouter already reports. Cursor is
   subscription-billed and disabled by default. Code Tribunal captures no token
   counts and maintains no prices.
5. **Coarse re-run advice.** The only re-run action is "re-run the review from
   prepare". This matches the existing guidance in
   [operations](../operations.md) and [troubleshooting](../TROUBLESHOOTING.md).
   Per-job retry advice is out of scope until
   [the open question](#open-questions) is answered.
6. **Present on every published run.** Post creates the summary comment on
   every run it publishes, including a full-health run with no findings. A
   failed panel is not published: post does not run, the red consensus check is
   the signal, and the consensus job log carries the health rendering.

## Scope

**In:** consensus reading status files; the `run_health` record and its grade;
the consensus job-log rendering; the CI template argument; summary rendering;
schema, type, golden and test updates.

**Out:** usage and cost capture; changes to `adapter_status.v1`; failed-panel
publication; critique-coverage or critique-effect counts; any critique-quality
policy; per-job retry automation; any new retry, quorum or gate policy; CI-API
job inspection; inline thread bodies; the `render-body` version; config keys.

## Consensus: the `run_health` record

### Inputs

Add `--status-dir` to the consensus CLI, defaulting to `out/status`. Consensus
reads `<reviewer>.json` and `critique-<reviewer>.json` for every enabled seat
and ignores every other file, including debug files. Wire it in both templates:

- GitHub: `--status-dir reviewer-artifacts/status`. The review and critique
  artifacts already deliver `status/` there.
- GitLab: `--status-dir out/status`. The job's `needs` already deliver it.

Update the installed GitHub workflow copy through the parity sync.

A status file is **usable** only if it validates against
`adapter_status.schema.json`, its `reviewer` and `stage` match its filename,
and its `run_id` equals the manifest's. Otherwise consensus treats it as absent.

### Per-job outcome

For each enabled seat and stage (review always; critique only when
`critique.enabled`), derive exactly one `outcome`. The first matching row wins:

| Condition | Review outcome | Critique outcome |
|---|---|---|
| A batch exists and is usable (`batch_usable_for_panel`, or a successful critique batch) | `success` | `success` |
| A batch exists, its status is `success`, and it is not usable | `unusable` (every finding rejected) | n/a |
| A non-success batch exists | its `adapter_status` (`timeout`, `model_error`, `schema_error`, `config_error`, `internal_error`) | same |
| No batch exists; a usable status file reports a non-success status | that status | same |
| No batch exists; a usable status file reports `success` | `batch_missing` | `batch_missing` |
| Neither a batch nor a usable status file exists | `missing` | `missing` |

Only a usable batch yields `success`. A status file never promotes a job; it
adds `duration_ms`, `error_class` and the error message to whatever outcome the
batches determine. If the batch and the status file disagree, the batch wins.
Every review job that isn't `success` is a seat the reducer counts as failed.

### Record shape

`run_health` is a new **optional** top-level property of `consensus.v2`. The
schema identifier does not change (see [Versioning](#versioning)).

- **Producer:** consensus from this release on always writes `run_health`.
- **Consumer:** post requires it. An artifact without `run_health` comes from an
  earlier release, so post rejects it before any platform mutation, with a
  message saying so. There is no fallback rendering, so no dual path and no
  temporary-compatibility row.

```json
"run_health": {
  "grade": "degraded",
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
- **`duration_ms`, `error_class`** come from a usable status file, or are
  `null` without one.
- **`error_message`** comes from `error_message_redacted`, normalized to one
  line and truncated to 300 characters by a renderer-independent rule.
- **`jobs` order:** review before critique, then by reviewer ID. Disabled seats
  are omitted.

### Grade

The derivation is one pure function beside `panel_status` in
[`consensus.py`](../../ai-review/src/ai_review/consensus.py). It reads only the
consensus fields and `jobs`. `panel_status` keeps its meaning and its exit-code
role; `grade` never feeds back into it.

1. `rerun_recommended` if any of these hold:
   - `panel_status == "failed"`;
   - **two-identity support is unreachable:** at least one review seat
     succeeded, but the union of reviewers with a usable review batch and
     critics with a successful critique batch has fewer than two identities;
   - critique is enabled and no critique job succeeded.
2. Otherwise `degraded` if any job is not `success`, or any dropped or
   cap-omitted count is non-zero.
3. Otherwise `full`.

The action is not stored. The renderer derives it from the grade and the job
outcomes (see [Rendering](#rendering)).

### Job log

Consensus prints a plain-text rendering of `run_health` to stdout on every run,
before it writes its artifact and before it returns. That includes a failed
panel, which today exits 3 silently. This is the failed-panel explanation:
the red consensus check plus its log.

## Rendering

[`summary_render.py`](../../ai-review/src/ai_review/summary_render.py) gains
`run_health_section(consensus)`, which renders first, directly after the
summary title. The section has three parts, in this order:

1. **Headline** (always visible). Grade, the counts of completed review and
   critique jobs, and the panel-wide raw, accepted, dropped and cap-omitted
   finding counts summed from `jobs`.
2. **Problems and action** (visible, and only when the grade is not `full`).
   - One bullet per non-`success` job, giving the stage, reviewer, outcome
     wording, duration against the limit, and the truncated error message.
   - Then one action line.
3. **Run details** (collapsed `<details>`). A table with one row per job, and a
   summary line giving the longest review and longest critique duration.

Error messages are rendered as list items below the table, never inside table
cells.

````markdown
**Review health: degraded — re-run optional.** 2 of 3 review seats and 3 of 3 critics completed. Findings: 41 raw, 37 accepted, 4 dropped, 0 omitted by the per-seat cap.

- `opencode` review: timed out after 29m55s (limit 30m00s).

Action: none required; findings from the remaining seats were evaluated. Re-run the review from prepare if `opencode` coverage matters for this change.

<details>
<summary>Run details · longest review 29m55s · longest critique 6m10s</summary>

| Stage | Reviewer | Model | Effort | Outcome | Time | Findings raw/acc/drop/cap |
|---|---|---|---|---|---|---|
| review | `claude` | `anthropic/claude-haiku-4.5` | medium | success | 12m04s | 18/16/2/0 |
| review | `codex` | `openai/gpt-6-luna` | low | success | 8m41s | 23/21/2/0 |
| review | `opencode` | `xiaomi/mimo-v2.6-flash` | default | timeout ⚠ near limit | 29m55s | — |
| critique | `claude` | … | … | success | 4m02s | 12 critiques |

</details>
````

**Headline and action**, first match wins:

| Condition | Headline | Action line |
|---|---|---|
| Any job's outcome is `config_error` | `Review health: configuration error — fix before re-running.` | Fix the reviewer configuration named above; re-running unchanged will fail the same way. |
| Grade `rerun_recommended` | `Review health: re-run recommended.` | Re-run the review from prepare; do not retry individual jobs or mix artifacts. |
| Grade `degraded` and some job is not `success` | `Review health: degraded — re-run optional.` | The sentence in the example above. |
| Grade `degraded` (drops or caps only) | `Review health: degraded.` | None. |
| Grade `full` | `Review health: full.` | None. |

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

**Formatting:** durations use `XmYYs`. `⚠ near limit` marks a duration of at
least 80% of the stage timeout; it is computed at render time and never changes
the grade. `—` means not applicable.

**Size:**

- The headline, problem bullets and action are reserved space. The existing
  drop loop truncates finding sections only.
- Keep the layout open for persisted state. The next major release moves state
  inside this comment (simplification item N04: one bot comment per merge
  request). Size the reserved health space so that state can later claim its
  own reserved block within the platform limit. Do not add a second
  always-present comment.
- The details block is bounded: at most eight rows, plus error text capped at
  300 characters per job. It is part of the reserved section, not a droppable
  section descriptor.

**Posting:** in [`posting.py`](../../ai-review/src/ai_review/posting.py),
`upsert_summary_comment` no longer returns early when there are no fallback or
FYI findings. When a later run recovers, the next upsert replaces the old body.

## Security

- **Status files are untrusted input to consensus.** They are written by the
  trusted runner, but in a job that also runs model CLIs with tool access.
  Schema validation and the run-ID binding apply. A forged status file can
  mislead only the health panel; batches remain the decision input. State this
  in the [security model](../SECURITY_MODEL.md).
- **All text renders literally.** Every model ID, reviewer ID and error message
  goes through `literal_span`.
- **Error text is redacted, then truncated.**
- **No sensitive data in the record.** It holds no prompts, model bodies,
  endpoints, credentials or provider session IDs.

## Versioning

2.0.2 ships `consensus.v2`, whose schema is closed
(`additionalProperties: false`). On 2026-10-13 the maintainer chose a
compatible minor release:

- `run_health` is an *optional* property of `consensus.v2`.
  `adapter_status.v1` is unchanged.
- `artifacts-and-schemas.md` documents that same-identifier optional widening is
  a minor change. This is sound because producer and consumer always run from
  one pinned image pair, and mixing artifacts across runs is already
  unsupported. 1.0.1 set the precedent by tightening the `consensus.v2` category
  enum in place.
- No old-version decoders and no dual paths, so no temporary-compatibility row.
- The summary marker stays `ai-review-summary:v1`. The body hash changes, so the
  next run refreshes each existing summary once. `render-body` is untouched.

**Status files versus S06.** If S06 lands in the next major release, it moves
`duration_ms` and the error fields into the batch envelope and deletes the
status-dir loader.

## Exact implementation surface

One pull request; the consensus and rendering boundaries ship together because
post requires the field the consensus change adds.

1. **Consensus.**
   - The `--status-dir` loader and validation, the per-job derivation, the
     grade function, and stdout rendering.
   - An optional `run_health` property on `consensus.schema.json`, keeping
     `consensus.v2`, plus types and goldens.
   - Both CI templates, plus the GitHub parity copy.
2. **Posting/rendering.**
   - `run_health_section` and unconditional summary creation.
   - Post rejects an artifact without `run_health`.
   - Update [artifacts and schemas](../reference/artifacts-and-schemas.md), the
     [configuration](../configuration.md) and [operations](../operations.md)
     docs on reading the panel, and [CHANGELOG](../../CHANGELOG.md).

## Acceptance criteria

- Every enabled review seat appears in `run_health.jobs`, and so does every
  critique seat when critique is enabled. Each carries model, effort, timeout,
  outcome and duration, and no field is invented.
- A job killed without writing artifacts appears as `missing`, with the
  CI-ceiling wording.
- A panel where every critic failed grades `rerun_recommended`.
- With one usable review seat out of three, the panel grades
  `rerun_recommended` without a successful critic from another identity, and
  `degraded` with one. `panel_status` stays `degraded` in both cases.
- A missing batch whose status file reports `success` grades the job
  `batch_missing`, never `success`.
- Removing or corrupting every status file leaves group decisions,
  `panel_status`, `failed_reviewers` and exit codes byte-identical. Only
  `run_health` changes.
- A `config_error` seat renders the configuration headline and action, never a
  re-run suggestion, even when the grade is `rerun_recommended`.
- A full run with no findings creates a summary comment whose only content is
  the health headline and the collapsed details.
- A failed panel prints the health rendering in the consensus job log.
- Repeating consensus and post with identical inputs yields identical artifacts
  and summary bytes.
- No CI or platform API call is added to consensus or post.

## Required tests

- `ai-review/tests/unit/test_consensus_cli.py`: `--status-dir` loading; ignoring
  debug files; a filename or run-ID mismatch is treated as absent; a missing
  status directory still produces consensus; a failed panel prints the health
  rendering before exit 3.
- `ai-review/tests/unit/test_consensus_reducer.py`: one test per grade rule,
  including all critics failed, one usable seat with and without a successful
  other-identity critic, a success status with no batch, and drops only; an
  invariance test that strips status files and asserts identical non-health
  output.
- `ai-review/tests/unit/test_panel_degradation.py`: grade derivation against the
  existing degraded and failed panel scenarios.
- `ai-review/tests/unit/test_schema_validation.py` and
  `ai-review/tests/unit/test_types_schema_alignment.py`: the optional
  `run_health` on `consensus.v2`, with its shape and bounds. A 2.0.2-shaped
  consensus artifact still validates, and `test_post.py` asserts that post
  rejects it before any platform call.
- `ai-review/tests/unit/test_summary_render.py`: every row of the headline
  table, including a config error on a `rerun_recommended` grade; hostile model
  IDs and error text render literally; error text never appears inside a table
  cell; the reserved section survives truncation at both platform limits.
- `ai-review/tests/unit/test_post.py`: a summary is created for a full run with
  no findings; recovery and idempotency.
- `ai-review/tests/unit/test_ci_template.py`: both templates pass
  `--status-dir`; the GitHub parity copy matches.
- `ai-review/tests/contract/test_golden_consensus.py`: goldens carry
  `run_health` and are stable under file-order permutation.

## Open questions

- **Per-job retry.** Can a single failed job be retried safely and still be
  accepted by consensus? A retried review job reads the same manifest, so its
  `run_id` matches, and its configuration digest matches only if no
  `AI_REVIEW_*` variable changed. On GitHub, it is unverified whether "re-run
  failed jobs" gives a later attempt access to earlier-attempt artifacts; on
  GitLab, the [evidence runbook](../evidence/RUNBOOK.md) warns against pipeline
  retry. Until both are verified, the panel advises only "re-run from prepare".
- **Status upload after a timeout kill.** Whether `if: always()` and
  `when: always` uploads run after a platform timeout kill decides how often
  `missing` appears versus a real `timeout`. It doesn't change the design.

## Decision record

**2026-10-16: narrowed to one stage.** The maintainer removed:

- **Usage and cost capture** (formerly stage (b)): per-CLI extractors, the Codex
  `--json` event sidecar, and optional `usage` on `adapter_status.v1`. Spend is
  already visible per key in OpenRouter.
- **Failed-panel publication** (formerly stage (c)): `continue-on-error` and
  exit-3 passthrough in both templates, and a health-only post. The red
  consensus check already signals the failure; the job-log rendering explains it.
- **Critique coverage** (formerly SPEC-60, absorbed 2026-10-13): per-group
  coverage counts, `groups_surfaced_by_critique_support` and
  `verdicts_by_critic`. They fed only a future policy that has no spec, and the
  per-critic job outcomes already show whether critique ran. SPEC-67's summary
  marker shows noise-demoted groups.
- **Stored action and reasons:** `recommended_action`, the closed `reasons` enum,
  the adapter-written `notes` enum, and the consensus-derived `near_timeout`,
  `status_unreadable` and `status_disagrees_with_batch` notes. The renderer
  derives the action, and "near limit" is computed at render time.

The full prior design is in git history: see this file at `55a9db6` on `main`.
