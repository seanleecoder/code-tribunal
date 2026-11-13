# Record a false positive as `refuted`, not `wontfix`

Status: proposed. Written against `main` at `8cd7c9e` (2026-11-13); line
references are to that revision.

## Problem

`/ai-review wontfix` is the only way to settle a finding a maintainer believes
is wrong, and it also means "true, risk accepted". The state ledger, the
summary and the `prior_decisions.v1` history given to reviewers and critics
cannot tell the two apart.

On PR #101 the maintainer disputed eight surfaced findings in writing and
settled them with `wontfix`. An independent validation on 2026-11-12 withdrew
all eight: four refuted by executed probes, four fixed by removal of the code.
None was an accepted risk; the ledger records all eight as accepted risks.

Existing `wontfix` records stay unchanged: their intent cannot be
reconstructed. This adds no new product dimension, so no ADR is needed
([ADR-0003](../decisions/0003-product-invariants-and-complexity-envelope.md)).

## Meaning

`refuted` means "a maintainer rejects this finding's validity". `wontfix` means
"not fixing this concern" and is intended for accepted risk.

`refuted` settles a finding exactly as `wontfix` does: no inline thread, the
discussion is resolved on transition, and the record is kept across reruns and
absence until an authorized later command. `reopen` restores `open/reopen`;
`resolve` and `wontfix` may replace it.

## Change

- **Command grammar and authorization.** `COMMAND_RE` becomes
  `resolve|wontfix|refuted|reopen` (`commands.py:19`), keeping
  case-insensitivity, the own-line rule, the last command within a note and
  chronological precedence across notes (`commands.py:74`, `:108`). The access
  rule, verification and ignored-command warnings are unchanged
  (`commands.py:28`, `:91`; [revision lifecycle](../reference/revision-lifecycle.md)).
  Unverifiable access never changes a disposition. No structured rationale
  field.
- **State contract.** `status` and `human_disposition` each gain `refuted`
  (`state.schema.json:80`, `:91`; `types.py:45`, `:52`), written as
  `{status: "refuted", human_disposition: "refuted"}`. Same v2 envelope and
  hash; no second schema or duplicate field. Normalization already preserves
  the values (`memory.py:98`, `:107`).
- **Every settled branch** treats `refuted` as it treats `wontfix`:
  - inline suppression (`state_plan.py:54-56`);
  - thread-resolution transitions (`:290-299`);
  - commands on absent or protected records (`:369-397`);
  - commands on matched groups (`:503-515`);
  - carry-over of a previous settled record (`:516-518`, which today checks
    only `wontfix`);
  - settled prompt history (`memory.py:444`).

  Compaction (`memory.py:262`) and posting's use of `InlinePlan.settled`
  (`posting.py:208`) already handle it; tests prove that rather than new
  branches. No title or path suppression; existing matching rules only.
- **Publication failures.** Resolution uses the existing platform operation. A
  failed resolve preserves the prior status and disposition, reports
  `partial_failed` and retries through the existing path (`posting.py:306`;
  `state_plan.py:323`). Refuted records count in the pre-mutation size
  reservation and overflow check (`state_plan.py:561`). A parsed command is not
  a persisted disposition.
- **Prompts and summary.** `prior_decisions.v1` items carry
  `status: "refuted"` in `settled` (`memory.py:437-449`); the projection
  already passes status to both stages (`prompt_render.py:45`, `:112`, `:193`).
  Prompt text and the visible summary do **not** change here. Prompt wording
  changes model behaviour and needs measured evaluation, and summary counts of
  settled records are a separate presentation change; each would be its own
  spec. `prior_decisions.v1` gains a value, not a field; it is an inline prompt
  contract with no schema file or external reader, so it keeps its version.

## Compatibility and release

Existing images reject a hash-valid v2 ledger containing either new value at
schema validation. They neither read it as `wontfix` nor start empty
(`memory.py:154-177`, `:242`), so prepare and post fail closed on that change.
New images read every old v2 ledger.

Ship it in **one release**. A schema-only reader release would not give a safe
rollback floor. Commands are re-collected from every note on each run, and an
image that ignores `refuted` selects the latest *recognized* command instead
(`commands.py:81-83`, `:109-112`). Planning applies that command before
preserving the persisted disposition (`state_plan.py:389-397`, `:507-515`), so
an earlier `wontfix`, `reopen` or `resolve` would override a newer `refuted`.
A reader that recognizes `refuted` but does not apply it would be a temporary
dual path; it is not added.

Separate pull requests, each one boundary:

1. this spec and its index entry;
2. the posting/state change: schema, types, planning, prior decisions, command
   grammar and their tests (the grammar and the state it writes are
   inseparable, as that PR states);
3. lifecycle docs, the operations rollback statement and `CHANGELOG.md`;
4. canary tooling.

They are released together once all land; no intermediate image is advertised
as the feature. There is no temporary alias, decoder, dual path or flag, so no
[temporary compatibility](../development/temporary-compatibility.md) row; any
that appears is registered there.

**Rollback statement.** Before any `refuted` command is posted, rollback to
the previous release is unaffected. After one is posted, any rollback to an
image without this change uses the procedure below.

## Rollback after `refuted` has been used

Documented in [operations](../operations.md). While still on the new images:

1. Pause automatic triggers and drain jobs.
2. Export every affected summary and ledger with note and author identities,
   as in the [state v2 migration](../operations.md#state-v2-migration).
3. Post an authorized `/ai-review wontfix` on each refuted finding and run
   controlled reviews until no ledger field contains `refuted`.
4. Read each ledger back and verify that the exact rollback image admits it.
5. Restore the old image pins and the workflow/template version, then resume.

This loses the distinction in the live ledger; the export and the discussions
keep it. An inaccessible thread blocks rollback until it is repaired, or
restored from backup with reconciliation. The ledger is never deleted to make
an old image run.

## Acceptance

Use the smallest existing suites: state planning, publish and revision
lifecycle, prompt rendering, schema and summary.

- **State:** a `refuted` fixture validates and round-trips with an unchanged
  hash. It plans as settled: no inline thread, resolution on transition, and
  kept across rerun, absence, regrouping, FYI, ambiguous and degraded runs and
  after compaction. `prior_decisions.json` lists it under `settled`, and both
  rendered prompts carry its status. The frozen pre-change schema rejects each
  new value independently.
- **Commands:**
  - transitions from `open` and `wontfix`, `reopen` after `refuted`,
    replacement by `resolve` or `wontfix`, and latest-command-wins across all
    four commands;
  - authorization accepted, denied and unverifiable on both platforms;
  - a failed resolve or unresolve preserves the prior state;
  - overflow fails before mutation;
  - GitHub and GitLab lifecycle end-to-end: a `refuted` record survives a
    rerun on a new revision with the same thread identity, and `reopen`
    restores it;
  - the rollback sequence yields a ledger the exact old image admits,
    including mixed historical commands.
- **Rollback integration:** threads with an earlier authorized `wontfix`,
  `reopen` or `resolve` followed by `refuted`, for a current and an absent
  finding. After the rollback procedure and the old image's command
  collection, planning and publication, each ends `wontfix` with its thread
  resolved and its identity unchanged. Negative control: without the procedure,
  the exact old image refuses the ledger at schema validation and fails closed
  with no writes.
- **Goldens:** reducer goldens are unchanged ([testing](../development/testing.md)).

## Canaries and verification

- The shared lifecycle driver and both platform adapters gain
  `refuted → unchanged rerun → reopen`, keeping the `wontfix` steps
  (`scripts/candidate_canary_common.py:394`, `:442`).
- The new steps assert saved status and disposition, resolved state, unchanged
  thread identity and no repeated mutations.
- Rehearse the rollback procedure on isolated GitHub and GitLab changes before
  the release.
- Run `make quality`, reporting Termux prerequisites explicitly.
- Rebuild the images, run the panel and lifecycle canaries, and publish through
  the signed-tag/digest process
  ([ADR-0004](../decisions/0004-simplified-release-process.md)).
- No trust boundary changes, so the hostile canary is not required unless
  authorization code changes beyond the grammar.

When implemented, delete this spec and close its index entry.
