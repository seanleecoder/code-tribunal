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

Retain these authoritative paths and schema files:

| Stage | Outcome path | Schema |
|---|---|---|
| Review | `out/findings/<reviewer>.json` | `finding_batch.schema.json` |
| Critique | `out/critiques/<reviewer>.json` | `critique_batch.schema.json` |

The current `finding_batch.v2` and `critique_batch.v2` are unreleased, so evolve
them in place for the same major cutover. The released 2.1.0 schemas use v1;
there is no supported released v2 shape to decode. Raw authoring contracts,
`pooled_findings.v2`, finding content and critique content stay as they are.

Both outcome schemas require the following metadata, supplied by the runner:

| Fields | Meaning |
|---|---|
| `schema_version` | The stage-specific batch version |
| `run_id`, `effective_config_sha256` | Binding to prepare's manifest |
| `adapter_status` | Existing `success`, `skipped`, `timeout`, `model_error`, `schema_error`, `config_error` or `internal_error` |
| `started_at`, `completed_at`, `duration_ms` | Execution timestamps and nonnegative monotonic duration |
| `error_class`, `error_message_redacted` | Nullable diagnostic fields; redact and bound before writing |

Review retains `reviewer`, `model`, `raw_finding_count`,
`dropped_finding_count` and `findings`. Critique retains `critic` and `critiques`.
The directory and schema identify the stage, so no extra `stage` or
self-referential `output_file` field is needed. Consumers obtain model/effort
and timeout settings from the admitted manifest configuration as they do today.

Remove persisted `accepted_finding_count` and `usable_for_resolution` from
review outcomes. One pure quality helper derives:

- accepted count: `len(findings)`;
- cap omissions: `raw_finding_count - len(findings) - dropped_finding_count`;
- review eligibility: `adapter_status == "success"` and either
  `raw_finding_count == 0` or at least one finding was accepted.

Counts must be nonnegative, and accepted plus dropped cannot exceed raw.
Validate these relationships at transport admission as well as at construction;
do not recreate an independent eligibility flag or status projection file.
Keep the existing derived count fields in consensus health rows, since those
are the operator-facing report rather than another input authority.

## Outcome handling

| Execution result | Outcome and downstream behavior |
|---|---|
| Valid empty review | `success`, zero counts and no findings; eligible |
| Valid nonempty review | `success`; accepted findings enter the pool and reducer |
| Some candidates invalid or capped | Retain accepted findings and raw/dropped accounting; health shows the loss and grades the run accordingly; current eligibility is preserved |
| Every reported candidate rejected | Nonzero raw count, no accepted findings; health says `unusable`; ineligible |
| Valid empty or nonempty critique | `success`; existing critique validation and contribution rules apply |
| Timeout, output budget failure, model or schema failure | Existing failure classification, empty stage payload; no evidence or resolution eligibility |
| Disabled seat | `skipped`, empty payload; excluded from the expected enabled roster |
| No outcome delivered | Health says `missing`; never synthesize a successful empty result |

Failure and skipped outcomes require an empty stage payload and zero review
counts. The failure status distinguishes those zeros from a genuine empty
success. Successful outcomes carry null error fields; loss during finding
normalization is recorded by the quality counts, not an invented failure flag.

An abnormal or interrupted execution must not become a successful empty
review. Timeout stdout remains bounded diagnostic material, not evidence.
This consolidation does not expand partial-output salvage or change the
existing handling of a completed CLI's nonzero exit with parseable stdout;
cover that behavior explicitly when migrating the runner tests. A stricter
exit-code policy is a separate decision.

When a valid prepare run/config binding is available, every handled execution
writes one complete outcome, including failures. Validate the outcome before
publishing it. Remove `_write_status`, the separate status constructor and the
status-only fallback. If the runner cannot establish a valid manifest binding,
exit nonzero with a bounded, redacted diagnostic and emit no authoritative
outcome. Do not invent `unknown-run`, nullable identity or a success placeholder
to make such a file admissible. A valid downstream manifest will report the
undelivered job as missing; an invalid manifest already prevents consensus.

Existing redacted parse/timeout debug files may remain under `out/status/` as
operator diagnostics. Their directory name does not make them outcome inputs.
Keep S09's private capture, budgets, process cleanup and diagnostic redaction.
Neither these files nor the on-disk critique pool are reloaded as evidence.

## Admission and authority

Treat all delivered outcomes as untrusted CI transport, including metadata
written in a job that also runs the model CLI. Critique pooling and consensus
must use the same admission rules:

1. Validate the stage-specific schema and count relationships.
2. Match the filename to `reviewer` or `critic`, and require a trusted registry
   identity. A review artifact cannot substitute for a critique artifact.
3. Check exact run and effective-config bindings for every present outcome,
   including failures. Reject duplicates, unknown seats, mismatched filenames,
   stale bindings and non-skipped results for disabled seats.
4. Compare delivered outcomes with the enabled roster in the manifest config.
   Missing files degrade execution health; malformed or misbound present files
   fail integrity validation rather than being silently discarded.

The runner supplies identity, execution metadata and quality accounting after
normalizing model output; models do not author those fields. Existing critique
short-ID binding to the in-memory pool remains intact. A critic whose review
failed may still supply valid independent critique.

Health derives duration and diagnostic text from the admitted outcome and
reapplies bounded redaction and workflow-command escaping before rendering.
It remains observational: health grades and diagnostic fields never feed
support counts, reducer decisions, resolution quorum or exit policy.

`batch_missing` ceases to be an emitted health outcome because batch and status
are now one artifact. Delete its branch, label and enum member from the
unreleased `consensus.v3` contract. Keep the remaining consensus fields, health
row layout and persisted state format. No platform client enters pure modules.

## Cutover and deletion

One implementation pull request changes the outcome schemas and writer,
critique pool admission, consensus/health consumers, both canonical workflow
invocations and artifact uploads, and packaged/canary readers together. Those
consumers currently read both files; splitting their migration would require a
temporary decoder or writer for both contracts. Updating them together avoids
that compatibility path. Keep the change centered on the adapter-to-consumer
artifact contract; it does not require a platform or state migration.

Delete `adapter_status.schema.json`, status JSON writing/loading,
`consensus --status-dir`, status mirrors in fixtures, and their obsolete
assertions. Update the local harness, packaged smoke probes,
`validate_candidate_canary.py`, both platform canaries and shared lifecycle
checks to read the complete outcome. Derive any existing canary summary counts
from its payload; do not turn this into a new canary summary schema or release
authority. Preserve bounded debug uploads even after removing status JSON
uploads. Refactor private helpers directly rather than retaining wrappers.

Upgrade the canonical GitHub workflow, GitLab include and both images together,
then run fresh artifacts from prepare. No old/new online decoder, alias,
feature flag or dual writer is planned. If implementation discovers a real
compatibility need, record it in
[temporary compatibility](../development/temporary-compatibility.md) with a
removal condition before adding it. Update the artifact reference, CLI docs,
architecture ownership, troubleshooting and breaking-change notes with the
implementation; this proposal is not current product documentation.

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
  integration cases, and import-boundary checks stay green. Review any golden
  diff; use `make update-golden` only for an intentional contract change.

Run `make quality` using the documented interpreter/profile and report missing
native parser/tool prerequisites with local results. Run packaged base/reviewer
smoke checks in their matching images. Before release, panel and lifecycle
campaigns on both platforms must exercise the consolidated artifacts on the
exact candidate images; include hostile evidence as required by the
[release checklist](../development/release-process.md#release-pull-request-checklist).

Report deleted paths and measured source/config changes without a deletion
quota. After implementation, update the active index and audit disposition and
delete this completed spec; history retains the proposal.
