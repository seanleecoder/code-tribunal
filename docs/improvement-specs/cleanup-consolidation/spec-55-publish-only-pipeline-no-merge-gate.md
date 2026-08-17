# SPEC-55 - Publish-only pipeline with no merge gate

- **Status:** Implemented; retained pending merge to `main`
- **Severity:** High workflow and policy simplification
- **Effort:** L
- **Historical implementation coordination:** This work was delivered with SPEC-54 rather than
  after it as a sequential dependency. It could not land as a standalone change because SPEC-54
  deleted `summary.block_merge` while this spec removed its only consumer. The two shipped as one
  change series, as [the package README](README.md) records.
- **Contract changes:** completes `review_config.v3`; deletes the gate artifact family
- **Breaking for operators:** removes the `gate` job that installation guides instruct
  repositories to configure as a required status check. See
  [Operator migration](#operator-migration).

## Objective

Make Code Tribunal an informational review publisher:

- every anchorable `surface` finding becomes a discussion thread;
- non-anchorable surfaced findings fall back to the owned summary;
- FYI findings remain summary-only;
- Code Tribunal never decides whether a change may merge;
- the pipeline reports only whether review publication completed successfully.

Delete the separate merge-gate implementation, configuration, artifact, workflow job, and
documentation.

## Product policy

A posted thread means:

> At least two independent reviewers supported this issue and it is worth human or agent
> attention.

It does not mean:

- request changes;
- merge blocked;
- human acknowledgement required;
- severity policy satisfied.

Repositories may independently choose to require conversation resolution, successful CI,
or another agent's approval. Those are repository policies, not Code Tribunal policy.

## Posting behavior

### Surface routing

| Consensus decision | Output |
|---|---|
| `surface`, valid reusable anchor | Create or update one inline thread |
| `surface`, unsupported/stale/unavailable anchor | Include in summary fallback |
| `fyi` | Include in FYI summary section |
| `drop` | Publish nothing |

The current thread lifecycle remains:

- update an existing bot-owned thread when content changes;
- leave unchanged content untouched;
- resolve a no-longer-present finding when resolution quorum is available;
- preserve human `resolve`, `wontfix`, and `reopen` dispositions;
- recover state according to existing policy.

These lifecycle operations are discussion management, not merge enforcement.

### Remove the normal surfaced-thread cap

Delete `limits.max_posted_surface_findings` and the branch that moves excess surface
findings into the summary merely because a configured count was reached.

The upstream `max_findings` limit on each reviewer remains the volume bound. Platform API
errors continue to degrade individual threads to summary fallback where possible and must be
reported in `post_result`.

Do not replace the deleted setting with another operator-visible thread cap. A future hard
safety ceiling, if proven necessary by platform limits, must fail explicitly as an
operational overflow rather than silently redefining surfaced findings as summary-only.

The branch to delete is `posting.py`'s post-sort overflow slice, which appends one
`surface fallback to summary: max_posted_surface_findings (N) reached` warning per displaced
group. `test_post.py` pins that warning string verbatim; delete the assertion with the branch.

### Keep `limits.max_fyi_findings`

`max_fyi_findings` is the sibling cap and it **stays**. The reasoning above does not extend to
it: the surface cap silently reclassified a finding from thread to summary, whereas the FYI cap
truncates a list that is already summary-only and already renders a visible "more" trailer. The
two are not the same defect. State this rather than leaving the asymmetry to be read as an
oversight.

### Decide `posting.fallback_to_summary_comment`

This spec's objective promises that non-anchorable surfaced findings fall back to the owned
summary. One configuration key currently breaks that promise more completely than the cap being
deleted: when `posting.fallback_to_summary_comment` is false, `posting.py` passes an empty
fallback list to the summary renderer, so every group diverted for an unanchorable anchor,
unsupported side, unsupported multiline range, ambiguous state match, or platform API error
disappears from **all** output. It survives only in persisted state and in `post_result.warnings`.

Deleting a cap that reclassifies findings while keeping a flag that erases them is not coherent.
Resolve it in this spec: either remove the key so summary fallback is unconditional, or keep it
and document plainly, in `docs/configuration.md`, that disabling it discards surfaced findings
that could not be anchored. Removal is preferred and consistent with SPEC-57's treatment of
`state.backend` — a pseudo-choice whose only reachable effect is to lose product output.

## Remove merge enforcement

Delete:

- `merge_gate` from configuration;
- `AI_REVIEW_MERGE_GATE_ENABLED` as an active override;
- `ai-review/src/ai_review/gate.py`;
- `ai-review/schemas/gate_result.schema.json`;
- `GateResult`, `GateStatus`, and related types;
- `ai-review/tests/unit/test_gate.py`;
- gate-specific portions of `test_post_gate_e2e.py`, but see
  [Splitting the post/gate end-to-end suite](#splitting-the-postgate-end-to-end-suite);
- the GitHub `gate` job;
- the GitLab `ai_review_gate` job;
- gate artifact upload/download paths;
- the `gate` entry in the base-image smoke loop in
  `.github/workflows/publish-ai-review-images.yml`. **This one fails the image publish, not the
  review pipeline**: the loop runs `python -m ai_review.<module> --help` over a fixed module list
  that includes `gate`, so deleting `gate.py` without editing it breaks image publication;
- gate result references in docs, release checks, examples, and troubleshooting, enumerated in
  [File-level impact](#file-level-impact);
- `block_merge` and acknowledgement rendering already removed by SPEC-54.

### Registries that name the gate outside `ai-review/src`

Three registries hold the gate by name and none of them live where a search for `gate.py` would
find them. All three are already listed under
[Release and validation tooling](#release-and-validation-tooling) or above except the first:

- `scripts/pipeline_trust.py` holds `ai_review_gate` in `RESERVED_DIRECT_JOB_NAMES`. This is the
  in-pipeline GitLab trusted-template auditor that runs *from the image* against a consuming
  project's configuration, so it is a trust boundary rather than a job list. **Keep
  `ai_review_gate` reserved** for one release after the job is deleted: a consumer pinned to an
  older template still declares the job, and un-reserving a name is the direction that loosens
  the boundary. Register the eventual removal under SPEC-59's temporary-compatibility table.
- `scripts/check_release_inputs.py` `GITHUB_CONTAINER_ROLES`;
- `scripts/check_supply_chain_pins.py` container counts.

### Deleting `GateResult` weakens an unrelated test

`ai-review/tests/unit/test_types_schema_alignment.py` registers `gate_result.schema.json` against
`GateResult` in its artifact map, and carries a deliberately wrong negative-control class whose
only purpose is to prove the alignment checker detects a scalar type mismatch. Deleting
`GateResult` therefore cannot be a pure deletion — re-point the negative control at another
artifact type so the checker keeps proving it works. Do not simply drop the control with the type.

### Splitting the post/gate end-to-end suite

`ai-review/tests/integration/test_post_gate_e2e.py` is two classes, and the second contains no
gate reference at all: it covers mock-scenario lifecycle, in-place discussion body updates, and
the cross-revision anchor remap contract. It is cited as evidence by
`docs/reference/revision-lifecycle.md`, `docs/evidence/README.md`, `docs/evidence/RUNBOOK.md`, and
`docs/evidence/record-github-current-image.md`. Two tests in the first class — GitHub rerun
idempotence and hostile-literal-body rerun idempotence across both platforms — likewise have no
gate assertion.

Split the file rather than trimming it, and **rename it**, because a file called
`test_post_gate_e2e.py` containing no gate is a worse outcome than the citation churn. Update the
four citing documents in the same change; the documentation link check will not catch a stale
filename in prose.

### Reconcile the open specs this invalidates

Delete SPEC-42 from the open-spec index. Its `wontfix` versus gate question no longer exists.
The human disposition itself remains supported.

Three further open specs contain contract text this spec invalidates, and
[the package README](README.md) assigns their reconciliation to this spec rather than to a
follow-up:

- SPEC-45 and SPEC-46 both read `block_merge` and quorum as given inputs;
- SPEC-46 additionally reasons about `max_posted_surface_findings`, deleted here;
- SPEC-47 and SPEC-48 both assign work to `gate.py`, and SPEC-48 specifies a
  `passed_no_reviewable_changes` gate outcome that will not exist.

Reconcile each against the informational contract. Do not leave a proposed spec describing a
consumer of a deleted artifact.

Also correct `docs/improvement-specs/README.md`. Beyond SPEC-42's table row, its "what to do next"
section names the post→gate end-to-end fixture as a regression gate to keep green.

## Operational job result

Keep the existing `post.py` and `post_result.v1` names. They still accurately describe the
operation and avoiding a rename prevents churn with no product benefit.

Change `post.py` exit behavior:

| `post_result.status` | Exit |
|---|---:|
| `success` | 0 |
| `stale_head` | 0 |
| `failed` | nonzero |
| `partial_failed` | nonzero |
| `state_overflow` | nonzero |

Findings of any severity must never cause a nonzero exit.

`stale_head` remains a successful no-op: a newer revision superseded the run, and no mutation
should occur.

The five rows above are the complete `post_result.status` enum today. Do not implement the table
as an exhaustive match that assumes it stays complete: **an unrecognized status must exit
nonzero**, so a future status added without revisiting `post.py` fails loudly instead of
reporting success.

`--dry-run` uses the same table. A dry run that reports an operational failure exits nonzero,
which is intended — the local lifecycle targets exercise the real posting and state path and
should not report success when it degrades.

### What the deleted gate did that nothing else does

Two behaviors disappear with `gate.py` and both are deliberate losses. Record the reasoning so a
later reader does not restore them by reflex:

- **The cross-artifact run-id check.** `evaluate_gate` re-verified that `post_result.run_id`
  matched `consensus.run_id` as SPEC-33 defense-in-depth, and `CHANGELOG.md` advertises it.
  It existed because the gate was the one stage that recombined two independently downloaded
  artifacts. Once `post` is terminal, no stage does: `post.py` derives its result from the
  consensus it loaded in the same process, so a mismatch is unreachable rather than merely
  unlikely.
- **Consumer-side validation of `post_result.json`.** The gate CLI was the only reader that
  validated the artifact against its schema. `post.py` already validates on write, and after this
  spec nothing reads `post_result.json` inside the pipeline. Keep the write-side validation; it is
  now the only one.

### GitHub workflow

- Remove the `gate` job.
- Keep `post` as the terminal product job.
- Use `if: always() && needs.prepare.result == 'success'` so `post` can run and report an
  upstream consensus failure instead of being silently skipped after a failed consensus job.
- Before artifact download, add a step that fails when `needs.consensus.result != 'success'`.
- Do not claim that requiring `post` covers an eligible run whose `prepare` job itself could
  not start. Code Tribunal is informational by default; the installation guide must describe
  this accurately.
- Do not recommend `Require conversation resolution` as a Code Tribunal requirement.
  Repositories may enable it as their own acknowledgement policy.

The workflow's review and critique matrix remains unchanged.

### GitLab workflow

- Remove `ai_review_gate`.
- Keep `post_ai_review` as the terminal product job.
- Posting failure remains a failed pipeline job unless the consuming project explicitly marks
  it allowed to fail.
- Do not require unresolved-thread enforcement in the template. Document it as an optional
  repository policy.
- **Do not mirror the GitHub `if: always()` change here.** `post_ai_review` keeps its plain
  `needs:` on `consensus_ai_review` and stays skipped when consensus fails. The asymmetry is
  intentional and follows from the platforms differing: GitLab enforces at pipeline level through
  **Pipelines must succeed**, so a failed `consensus_ai_review` already blocks, while GitHub
  enforces per job, where a skipped required check does not. Stating this prevents a later change
  that "restores symmetry" and adds an unreachable guard step.
- Keep `post_ai_review`'s `resource_group`. It serializes concurrent posting for one merge
  request and is unrelated to the gate.

## Operator migration

Deleting the `gate` job is a breaking change for every existing installation, and it is the one
change in this spec that can break a consumer that never upgrades a line of their own code.

The installation guides instruct repositories to add `gate` as a required status check, and at
least one live consumer ruleset recorded under `docs/evidence/` does exactly that. On GitHub, a
required status check that never reports leaves pull requests permanently unmergeable — the
workflow does not fail, it simply never produces the check the ruleset waits for.

Required:

- a `CHANGELOG.md` breaking-change entry that names the `gate` check explicitly and states that
  branch protection or ruleset entries referencing it must be removed **before or together with**
  the workflow upgrade;
- a migration note in `docs/getting-started/github.md` replacing the "Require the gate" section:
  Code Tribunal is informational and requires no status check; a repository that wants the review
  to have run before merge may require `post` instead, with the caveat below;
- the same treatment for `docs/getting-started/gitlab.md`;
- an uninstall/rollback note, since `docs/getting-started/github.md` currently tells operators to
  remove "the AI Review required check" by a name that no longer exists.

Do not claim that requiring `post` is equivalent to the deleted gate. It is not: it reports on
publication, not on findings, and it cannot cover a run whose `prepare` job never started.

## Configuration migration

SPEC-54 through SPEC-57 define one `review_config.v3` shape.

Remove:

- top-level `merge_gate`, its allowed-key set, and its validation branch;
- `AI_REVIEW_MERGE_GATE_ENABLED` from active overrides and workflow environment;
- `merge_gate_enabled` from `effective_config_summary()`.

### The effective-config digest changes for every run

`effective_config_summary()` is canonically hashed into `effective_config_sha256`, written into
the prepare manifest, carried by every adapter artifact, and re-derived by consensus as a
cross-job drift detector. Dropping `merge_gate_enabled` therefore changes the digest of every
configuration, including one whose YAML the operator never touched.

The consequence is operational, not theoretical: a pipeline that mixes a pre-upgrade prepare
manifest with post-upgrade consensus fails the drift check. Say so in the release notes —
**in-flight runs must be restarted from `prepare` after upgrading**, not resumed. SPEC-57 makes
the same change for `state_backend`; if the two land in one release the digest moves once.

### Retiring `AI_REVIEW_MERGE_GATE_ENABLED`

The variable must fail loudly rather than appear effective. The retirement mechanism has four
parts and three of them are enforced by the documentation check, which runs **first** in
`make quality` — miss them and the build fails before any test executes:

1. add the name to `RETIRED_ENV_OVERRIDES` in `config.py`, which raises for any retired name at
   the top of `apply_env_overrides`;
2. add it to `REJECTED_ENV_NAMES` in `scripts/check_docs.py`, or the documentation inventory
   reports it as an inert environment name;
3. add a row to the rejected-variables table in `docs/configuration.md`;
4. **remove** its row from the supported-controls table in the same file.

The existing test asserting that every retired override raises covers the new entry automatically;
its stated intent is that a retired override must not become a no-op.

Note the ordering SPEC-54 already records: environment overrides are applied before configuration
validation, so a document that still sets the retired variable reports the env-var error rather
than the v3 migration message. Write the test expectations for that order.

Track the eventual deletion under SPEC-59's temporary-compatibility process. If SPEC-59 has not
landed when this spec does, the tombstone still needs a named owner and removal target — record
it in the changelog entry rather than leaving it untracked.

## Rendering changes

Replace the current footer fields:

```text
- Decision: ...
- Blocking: ...
- Human acknowledgment: ...
```

with informational support fields:

```text
Support:
- Direct reviewers: <sorted contributors>
- Agreeing critics: <sorted critics or none>
- Independent support: <support_count>
- Status: surfaced for discussion
- Merge decision: left to maintainers and downstream automation
```

The highest severity header may read `BLOCKER`, but the footer must make its informational
meaning unambiguous.

`render_body`'s `successful_reviewer_count` parameter exists only to supply the denominator in
`Direct votes: <n>/<total>`. SPEC-54 removes `vote_count` and the replacement footer has no
denominator, so the parameter becomes dead. Remove it and its call sites as part of this change;
this spec owns the footer, so it owns the resulting signature.

### The section heading is part of the note grammar

Renaming the footer heading from `Consensus:` to `Support:` is not a cosmetic edit.
`ai-review/src/ai_review/notes.py` holds a `REVIEW_SECTION_BOUNDARIES` frozen set —
`Evidence:`, `Dissent:`, `Suggestion:`, `Consensus:` — that `parse_review_note` uses to
decompose an existing bot thread body. That parser is on the marker-recovery path taken when the
persisted state note is missing, so it reads **pre-upgrade** bodies.

Therefore:

- update `REVIEW_SECTION_BOUNDARIES` in the same change as the footer;
- keep `Consensus:` in the set alongside `Support:` for one release, because recovery must still
  decompose threads written by the previous version. Register that carry-over in SPEC-59's
  temporary-compatibility table with a removal target;
- add a round-trip test in the existing note-parsing suite covering both headings.

Omitting this does not raise — the new footer simply parses as body content.

### Dissent must survive truncation

**Preserve the minority dissent section.** The renderer already emits a `Dissent:` block built
from `critique_disputes`, immediately above the footer this section replaces. It is not part of
the footer and must survive the rewrite unchanged: every surfaced finding continues to show its
minority dissent — critic identity, rationale, and any adjusted severity — even when
`support_count` is two or more. SPEC-54 makes this a requirement on this renderer; consensus that
erases the argument against it is not consensus. Add a test that a surfaced group with two
supporters and one dissenting critic renders both the support block and the dissent block.

"Unchanged" is not sufficient here, because the current arrangement already violates the
requirement in one reachable case. The renderer splits the body into a truncatable fragment list
and a reserved suffix. The footer is the reserved suffix and is never truncated; the dissent block
is an ordinary fragment, so when the composed body exceeds the platform comment limit the
fragment limiter can drop dissent while the footer survives. The replacement footer is **longer**
than the one it replaces, which shrinks the fragment budget further.

Fix the ordering as part of this spec: dissent must be reserved alongside the footer, or must rank
above evidence and suggestion in the fragment limiter, so that a body under pressure loses
supporting detail before it loses the argument against the finding. Add a test that a surfaced
group whose rendered body exceeds the platform comment limit still contains its dissent block.
Without it, the acceptance criterion above is unverifiable.

### Body-hash migration

Update body-hash goldens because the visible body changes. Do not preserve the old body hash for
thread-update compatibility; existing bot threads should update once to the new format. Thread
identity lives in `issue_id` and the marker, not in the hash, so this produces one round of
in-place updates and no duplicates.

`RENDER_BODY_VERSION` moved from `render-body.v3` to `render-body.v4` so the format change was
explicit in the hash input rather than implicit in the footer text. **SPEC-54 owned that bump**
and states it in its own rendering section; this spec inherited it and did not bump again. The
two specs shipped as one series, so the version moved exactly once. Splitting them during
implementation would otherwise have made the second instruction a no-op or landed on
`render-body.v5`.

Remember that `ai-review/tests/fixtures/golden/render_body_hostile.json` pins the entire footer
text and the body hash, and `make update-golden` does **not** regenerate it. It is maintained by
hand.

## Release and validation tooling

Update all fixed job-role assumptions, including at minimum:

- `scripts/check_release_inputs.py` GitHub container role registry;
- `scripts/check_supply_chain_pins.py` expected GitHub job-container counts;
- workflow template tests;
- release manifest tests;
- artifact/schema reference documentation.

The expected GitHub container jobs after deletion are:

- base image: `prepare`, `consensus`, `post`;
- reviewer image: `review`, `critique`.

So `check_supply_chain_pins.py`'s hardcoded expectation moves from six containers (four base,
two reviewer) to five (three base, two reviewer), and `GITHUB_CONTAINER_ROLES` loses its `gate`
entry. The template test that asserts the canonical workflow *contains* `python -m ai_review.gate`
must be inverted rather than deleted, so a reintroduced gate job fails the build.

Keep one canonical GitHub workflow and the existing byte-parity mechanism. This spec changes no
parity machinery; run `make workflow-parity` so the installed copy is regenerated byte-for-byte.

**Boundary with SPEC-56.** SPEC-56 introduces a single reviewer-seat parity test and requires it
to be the only one of its kind. That covers *seat sets* — which reviewers appear in the review and
critique matrices and the shipped config. This spec owns *job roles and container counts*. They do
not overlap; do not fold one into the other.

Before deleting `ai-review/schemas/gate_result.schema.json`, confirm that the frozen hashed file
sets in `release/history/*-release-inputs.json` — which list it — are not re-validated against the
current tree by `scripts/check_release_inputs.py`. Those are historical records of released
artifacts and must not be rewritten.

## File-level impact

The sections above describe the change; this list exists because the gate is named in more places
than a search for `gate.py` reaches. At minimum inspect and update:

**Runtime and schemas**

- `ai-review/src/ai_review/gate.py` and `ai-review/schemas/gate_result.schema.json` — deleted;
- `ai-review/src/ai_review/types.py` — `GateResult`, `GateStatus`;
- `ai-review/src/ai_review/config.py` — `merge_gate` keys, the env override, the validation
  branch, and `effective_config_summary()`;
- `ai-review/src/ai_review/posting.py` — the surface-cap branch and the summary-fallback flag;
- `ai-review/src/ai_review/render.py` — the footer, the fragment ordering for dissent, and the
  `render_body` signature;
- `ai-review/src/ai_review/notes.py` — `REVIEW_SECTION_BOUNDARIES`;
- `ai-review/src/ai_review/mock_reviewer.py` — two mock finding bodies whose visible text names
  the merge gate;
- `ai-review/config/review.yaml`.

**Workflows, scripts, templates**

- `ai-review/ci/review.github-actions.yml` and its byte-identical installed copy;
- `ai-review/ci/review.gitlab-ci.yml`;
- `.github/workflows/publish-ai-review-images.yml` — the base-image smoke module loop;
- `.github/ISSUE_TEMPLATE/bug_report.yml` — `gate` in the pipeline-stage dropdown;
- `scripts/pipeline_trust.py`, `scripts/check_release_inputs.py`,
  `scripts/check_supply_chain_pins.py`, `scripts/check_docs.py`.

**Documentation**

Each of these is a distinct authority, not a mention to sweep:

- `docs/getting-started/github.md` and `docs/getting-started/gitlab.md` — the "Require the gate"
  sections, first-run verification, and uninstall;
- `docs/reference/cli-and-exit-codes.md` — the gate CLI row **and** post's documented exit
  contract, which this spec rewrites. A documented command that no longer resolves fails the
  documentation contract test;
- `docs/reference/artifacts-and-schemas.md` — the gate artifact rows; a link to a deleted schema
  fails the documentation link check;
- `docs/reference/platform-differences.md` — the merge-enforcement row;
- `docs/operations.md` — the failure-behavior table keyed on gate exit 7 and the fail-closed
  paragraph that cites the gate tests;
- `docs/TROUBLESHOOTING.md` — see below;
- `docs/SECURITY_MODEL.md` — it names the merge-gate result as part of the trusted surface;
  removing it narrows the stated security model and that narrowing should be deliberate;
- `docs/development/architecture.md`, `docs/development/testing.md`, and
  `docs/development/release-process.md`, the last of which maps `gate.py` to `test_gate.py` as
  required release evidence;
- `docs/configuration.md` — the `merge_gate.enabled` row, the environment-variable rows, the
  section heading, and the stage list. Its key inventory is enforced and runs before the tests;
- `docs/decisions/0002-post-1.0-review-output-policy.md` — an **accepted** ADR that describes the
  gate as a consuming stage and as a consumer of `summary.block_merge`. Amend or supersede it.
  Preserve its surrounding point: consensus-artifact integrity between pipeline stages remains
  unaddressed, and that is still true after the gate is gone;
- `README.md` and `ai-review/README.md`;
- `ai-review/EXAMPLE_PIPELINE_WALKTHROUGH.md` — a "Stage 6 — Gate" narrative with a worked
  blocking outcome;
- `CHANGELOG.md` — the breaking-change entry required by
  [Operator migration](#operator-migration).

### Troubleshooting entries are replaced, not deleted

Two rows are gate-specific and one is adjacent. Replace the gate exit-7 row with the equivalent
`post` nonzero-exit row, and correct the row whose cause list still names the advisory-only panel
that SPEC-54 removes.

Add a row for the case this spec makes common: a pipeline that is green with no threads posted
because findings had only one independent supporter and are therefore FYI. Under the previous
policy that was unusual; under this one it is the expected outcome for an unconfirmed finding, and
an operator with no row to read will file it as a bug.

### Evidence records: amend procedures, do not rewrite history

`docs/evidence/` contains two kinds of file and they must be treated differently:

- **Live procedures** — the runbook and consumer-project setup guidance — instruct a reader to
  verify that the required check genuinely blocks. Those steps stop being executable and must be
  amended.
- **Historical records** — the per-image and per-run `record-*.md` files, including the hostile-MR
  probe that forged a `gate_result.json` — record what a released image actually did on a given
  date. **Do not rewrite them.** Where a record would otherwise read as current guidance, annotate
  it with the release in which the gate was removed. Falsifying an evidence record to match
  current code destroys the only thing it is for.

## Tests

Add or update tests for:

- every anchorable surface group creates or updates a thread regardless of severity;
- a surfaced group with two or more supporters and one dissenting critic renders both the support
  block and the minority dissent block;
- a single unsupported finding remains FYI summary-only;
- unanchorable surface findings use summary fallback;
- no normal surface-count cap diverts findings;
- a surfaced group whose rendered body exceeds the platform comment limit still contains its
  dissent block;
- `parse_review_note` round-trips a body carrying the new `Support:` heading **and** a body
  carrying the pre-upgrade `Consensus:` heading;
- `post.py` returns nonzero for operational failure statuses and zero for findings;
- `post.py` returns nonzero for an unrecognized status value;
- stale head performs no mutation and exits zero;
- GitHub and GitLab templates contain no gate job, asserted in the direction that fails if a gate
  job is reintroduced;
- the base-image smoke module list in the publish workflow resolves for every module it names;
- no gate schema or gate type remains in current runtime references;
- the schema/type alignment negative control still proves the checker detects a scalar mismatch
  after `GateResult` is removed;
- `AI_REVIEW_MERGE_GATE_ENABLED` fails with migration guidance;
- workflow parity still detects and repairs drift;
- current docs do not instruct users to require the removed gate;
- current docs describe conversation-resolution rules as optional repository policy;
- every command documented in the CLI reference resolves.

## Acceptance criteria

- Code Tribunal contains no finding-based merge decision.
- Every anchorable `surface` finding is represented by a thread.
- `blocker` severity has no effect on process exit or platform review state.
- The separate gate module, schema, type, artifact, jobs, and tests are deleted.
- Posting operational failures remain visible as failed jobs.
- The GitHub and GitLab canonical templates pass their structural and pin checks.
- `post_result` remains schema-valid and sufficient for operational diagnostics.
- No surfaced finding can be erased by body truncation, and no surfaced finding can be discarded
  by a configuration flag without that outcome being documented.
- Thread bodies written by the previous release still parse after the footer rename.
- The image publish workflow succeeds against an image built without `gate.py`.
- The changelog names the removal of the `gate` required status check as a breaking change, and
  the installation guides no longer instruct operators to configure it.
- No open specification describes a consumer of a deleted artifact.
- `make quality` passes, including the documentation checks that run before the tests.

## Non-goals

- Do not remove persistent state or human thread commands.
- Do not add a replacement merge-check API call.
- Do not submit a GitHub `REQUEST_CHANGES` review.
- Do not automatically enable GitHub or GitLab conversation-resolution requirements.
- Do not add critique-quality enforcement; SPEC-60 is observational only. Note that SPEC-60's
  prohibitions on changing the post exit status and on restoring a merge gate describe the
  contract **this** spec establishes, even though SPEC-60's header names only SPEC-54. SPEC-60
  must not land before this spec.
- Do not rewrite historical evidence records to match the new behavior.
- Do not reintroduce the deleted run-id binding check in another stage. It was specific to the
  gate recombining two independently downloaded artifacts.
- Do not bump `RENDER_BODY_VERSION` a second time; SPEC-54 owns the single bump.
