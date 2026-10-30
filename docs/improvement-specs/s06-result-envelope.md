# S06 — One outcome artifact per reviewer and stage

- **Status:** Proposed; implementation has not started.
- **Target:** The next major release after 2.1.x.
- **Baseline:** `main` at `82ef7c9`, reviewed on 2026-10-30.
- **Origin:** [S06 in the simplification audit](simplification-audit/README.md).

## Problem and scope

The runner writes a finding or critique batch and a separate
`adapter_status.v1` file for the same execution. Identity, status, timing and
finding counts overlap. Failure handling has both empty-batch-plus-status and
status-only paths. Consensus reads both artifacts, gives the batch precedence,
and must distinguish a missing batch from a missing status.

Make each existing batch the complete outcome of its execution. Keep the
existing finding and critique artifact families and paths; absorb status
metadata and delete the status mirror. This proposal consolidates existing
artifacts within [ADR-0003](../decisions/0003-product-invariants-and-complexity-envelope.md),
without adding a public artifact family or another decision authority.

Eligibility and closure policy remain as implemented. In particular, mixed
valid/invalid findings can remain usable while health reports degraded output.
S11 severity negotiation, S13 absence-based resolution, S07 launchers and S08
codec fallbacks require their own changes. The S12 reflection-checker cleanup
can follow this contract change. Removing the identical consensus
`successful_reviewers`/`resolution_eligible_reviewers` lists is also deferred;
it can be staged independently with their posting/state consumers.

## Proposed artifact contract

Keep the review (`out/findings/<reviewer>.json`) and critique
(`out/critiques/<reviewer>.json`) outcome paths and schema files listed in the
[artifact reference](../reference/artifacts-and-schemas.md).

The current `finding_batch.v2` and `critique_batch.v2` are unreleased, so evolve
them in place for the same major cutover. The released 2.1.0 schemas use v1;
there is no supported released v2 shape to decode. Raw authoring contracts,
`pooled_findings.v2`, finding content and critique content stay as they are.

Both batches already require `schema_version`, `run_id`,
`effective_config_sha256` and the existing `adapter_status` enum; review
batches also require `started_at` and `completed_at`. Add these
runner-supplied fields:

| Fields | Stage | Meaning |
|---|---|---|
| `started_at`, `completed_at` | Critique | Execution timestamps, as in review |
| `duration_ms` | Both | Nonnegative monotonic duration |
| `error_class`, `error_message_redacted` | Both | Nullable diagnostics; redact and bound before writing |

Review retains `reviewer`, `model`, `raw_finding_count`,
`dropped_finding_count` and `findings`; critique retains `critic` and
`critiques`. Do not add `stage`, `output_file` or settings fields: the path
identifies the stage, and the admitted manifest configuration supplies
model/effort and timeouts.

Remove persisted `accepted_finding_count` and `usable_for_resolution` from
review outcomes. One pure quality helper derives:

- accepted count: `len(findings)`;
- cap omissions: `raw_finding_count - len(findings) - dropped_finding_count`;
- review eligibility: `adapter_status == "success"` and either
  `raw_finding_count == 0` or at least one finding was accepted.

Critique pool selection, consensus and health all use that helper. Counts must
be nonnegative, and accepted plus dropped cannot exceed raw. Express the
status-dependent payload, count and error-field rules below as `if`/`then`
conditionals in both schemas; one `validate_outcome` function adds the count
arithmetic. Outcome constructors return validated outcomes, the runner publishes
them without revalidating, and admission calls the same function. Consensus
health rows keep their derived count fields as the operator-facing report, not
another input authority.

## Outcome handling

| Execution result | Outcome and downstream behavior |
|---|---|
| Valid empty review | `success`, zero counts and no findings; eligible |
| Valid nonempty review | `success`; accepted findings enter the pool and reducer |
| Some candidates invalid or capped | Retain accepted findings and raw/dropped accounting; health shows the loss and grades the run accordingly |
| Every reported candidate rejected | Nonzero raw count, no accepted findings; health says `unusable`; ineligible |
| Valid empty or nonempty critique | `success`; existing critique validation and contribution rules apply |
| Timeout, output budget failure, model or schema failure | Existing failure classification; no evidence or resolution eligibility |
| Disabled seat | `skipped`; excluded from the expected enabled roster |
| No outcome delivered | Health says `missing`; never synthesize a successful empty result |

Failure and skipped outcomes carry an empty payload and zero review counts; the
status distinguishes them from an empty success. Successful outcomes carry null
error fields; normalization loss is recorded by the quality counts, not an
invented failure flag.

Timeout stdout remains bounded diagnostic material, not evidence. This
consolidation does not expand partial-output salvage or change the existing
handling of a completed CLI's nonzero exit with parseable stdout; cover that
behavior explicitly when migrating the runner tests. A stricter exit-code
policy is a separate decision.

When a valid prepare run/config binding is available, every handled execution
writes one complete outcome, including failures. Remove `_write_status`, the
separate status constructor and the status-only fallback. If the runner cannot
establish a valid manifest binding, exit nonzero with a bounded, redacted
diagnostic and emit no authoritative outcome. Do not invent `unknown-run`,
nullable identity or a success placeholder to make such a file admissible. A
valid downstream manifest will report the undelivered job as missing; an
invalid manifest already prevents consensus.

Redacted parse/timeout debug files stay under `out/status/` and in uploads as
operator diagnostics; like the on-disk critique pool, they are never reloaded
as evidence. S09's capture, budgets, cleanup and redaction are unchanged.

## Admission and authority

Delivered outcomes are untrusted CI transport, as the
[security model](../SECURITY_MODEL.md#state-authenticity-and-integrity)
describes for status files today, including metadata written in a job that
also runs the model CLI. One outcome loader reads a stage directory, binds each
file to its filename and admits it through `validate_outcome`. Critique pooling
and consensus both call it, replacing today's separate finding and critique
loops. Admission:

1. Validates the stage-specific schema and count relationships.
2. Matches the filename to `reviewer` or `critic` and requires a trusted
   registry identity. A review artifact cannot substitute for a critique
   artifact.
3. Checks exact run and effective-config bindings for every present outcome,
   including failures. It rejects duplicates, unknown seats, mismatched
   filenames, stale bindings and non-skipped results for disabled seats.
4. In consensus only, compares delivered outcomes with the enabled roster in
   the manifest config. Missing files degrade execution health; malformed or
   misbound present files fail integrity validation, where a bad status file
   counts as absent today.

Models do not author identity, execution metadata or quality accounting.
Existing critique short-ID binding to the in-memory pool remains intact. A
critic whose review failed may still supply valid independent critique.

Health reads no files: it derives duration and diagnostic text from the
outcomes consensus admitted, and reapplies bounded redaction and
workflow-command escaping before rendering. It remains observational: health
grades and diagnostic fields never feed support counts, reducer decisions,
resolution quorum or exit policy.

`batch_missing` ceases to be an emitted health outcome because batch and status
are now one artifact. Delete its branch, label and enum member from the
unreleased `consensus.v3` contract. Keep the remaining consensus fields, health
row layout and persisted state format.

## Cutover and deletion

One implementation pull request migrates the outcome schemas and writer,
critique pool admission, consensus/health consumers, both canonical workflow
invocations and artifact uploads, both images, and packaged/canary readers,
then runs fresh artifacts from prepare. Those consumers read both files today,
so moving them together needs no decoder, alias, feature flag or dual writer.
If implementation discovers a real compatibility need, record it in
[temporary compatibility](../development/temporary-compatibility.md) with a
removal condition before adding it. The change requires no platform or state
migration.

Delete `adapter_status.schema.json`, status JSON writing, loading and uploads,
`consensus --status-dir`, status mirrors in fixtures, and their obsolete
assertions. Update the local harness, packaged smoke probes,
[`validate_candidate_canary.py`](../../scripts/validate_candidate_canary.py),
both platform canaries and shared lifecycle checks to read the complete
outcome. Derive any existing canary summary counts from its payload; do not
turn this into a new canary summary schema or release authority.

Update the artifact reference, security model, CLI docs, architecture
ownership, troubleshooting and breaking-change notes with the implementation.

## Acceptance and verification

Extend the smallest existing tests at each owning boundary:

- Runner/schema: each outcome in the table, complete timing/error metadata,
  binding failure before execution, redaction and exactly one outcome file.
  A truncated or malformed file must fail admission, not count as empty success.
- Integrity/pooling: wrong run, config, filename, stage, seat and duplicate;
  disabled skipped seats; missing review results and valid independent critique.
- Quality/health: empty success, all-invalid, mixed invalid, caps, timeout and
  missing transport remain distinct. Derived accepted/omitted counts agree
  with the payload. Diagnostic text cannot affect a finding decision.
- Workflow/packaging/canary: remove every status-JSON dependency and
  `--status-dir` invocation while keeping failure diagnostics available.
- Regression: existing consensus goldens, publish and revision-lifecycle
  integration cases, and import-boundary checks stay green; review any golden
  diff per the [testing guide](../development/testing.md).

Run `make quality` per the
[setup guide](../development/setup.md#termux-development-profile) and the
packaged base/reviewer smoke checks in their matching images. Before release,
panel and lifecycle campaigns on both platforms must exercise the consolidated
artifacts on the exact candidate images; include hostile evidence as required
by the [release checklist](../development/release-process.md#release-pull-request-checklist).

Report deleted paths and measured source/config changes without a deletion
quota.
