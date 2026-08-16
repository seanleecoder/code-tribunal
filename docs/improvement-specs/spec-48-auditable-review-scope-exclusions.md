# SPEC-48 — Auditable review-scope exclusions

- **Status:** Proposed (post-1.0; not a current product feature).
- **Severity:** High (a silent omission of changed code is a false-clean review result).
- **Effort:** L.
- **Depends on:** [SPEC-47](spec-47-trusted-project-review-config.md), as a hard prerequisite. The exclusions below are valid only when they arrive through SPEC-47's immutable target-revision policy and effective-config binding.
- **Related work:** the existing complete-diff retrieval and snapshot-containment contracts; this specification preserves their fail-closed behavior rather than adding a platform-generated-file shortcut.

## Rationale

A single generated artifact or lockfile can dominate an otherwise reviewable PR/MR and
push the whole diff past a local review limit. Raising the limit globally, trusting a
platform generated_file flag, or silently dropping the file is not an adequate
answer. Each either expands cost/exposure without a policy decision or hides which
changed code was not reviewed.

An adopter may reasonably decide that a checked-in generated directory, lockfile, or
vendored subtree has another review and validation path. That decision is still a
coverage reduction. It must be explicit, trusted, deterministic, visible in the
input and outcome artifacts, and incapable of being made by the PR/MR head that
receives secrets.

This specification adds a small, policy-configured exclusion language. It first
obtains and verifies the complete platform diff, then removes whole eligible diff
blocks before local limits, prompt construction, and snapshot copying. It reports
what was fetched, what was reviewed, what was excluded, and why. Remaining
reviewable scope still has the same limits and fails explicitly when too large.

The design deliberately treats an all-excluded change as a real outcome, not a green
absence of artifacts. That outcome makes no model call, creates no snapshot, does not
resolve existing state, does not post, and emits a passing gate result that says why.

## Scope and non-goals

**In scope**

- A strict review_scope.exclusions policy supplied only through SPEC-47's trusted
  complete project configuration.
- Repository-relative Gitignore-like matching with auditable rule/category/reason
  selection.
- Full platform-diff collection and completeness validation before filtering.
- Whole-diff-block filtering before local limits, prompt rendering, snapshot
  construction, and model execution.
- Input, consensus, post, and gate provenance for complete source scope, reviewed
  scope, excluded paths, byte counts, policy, and no-reviewable-changes outcomes.
- Scope-aware finding validation and absence-based state resolution.
- GitHub/GitLab templates, schemas/types, documentation, supply-chain rollout, and
  regression coverage.

**Out of scope**

- Any configuration source other than SPEC-47's base-revision-resolved
  inputs/config.review.yaml. No AI_REVIEW environment override, workflow variable,
  platform metadata bit, or PR/MR head file can exclude scope.
- Automatic global classification of generated, lockfile, or vendored files.
  Platform generated_file metadata may be diagnostic evidence but never policy.
- Negated patterns or re-inclusion. A pattern beginning with ! is rejected in v1.
- Per-hunk, line-level, chunk-level, or partial-file exclusions. A diff block is
  reviewed whole or excluded whole.
- Loosening complete-diff retrieval, accepting truncated/collapsed/overflowing
  platform responses, or treating an excluded path as permission to ignore an
  incomplete response.
- Raising limits, splitting source files, changing reviewer/consensus policy, or
  changing the semantics of an ordinary full-scope run beyond the explicit
  reviewable-path boundary below.
- Auto-resolving findings because their file became excluded. Scope policy controls
  what is reviewed now; it cannot silently erase prior review state.

## Exact contract

### 1. Trusted configuration shape

The optional review_scope object is a policy-owned field under the complete
configuration selected by SPEC-47. Its default is an empty exclusions array. It has
no environment form.

~~~yaml
review_scope:
  exclusions:
    - pattern: "generated/**"
      category: generated
      reason: "Regenerated from the checked-in schema."
~~~

review_scope permits only exclusions. exclusions is an ordered array; each entry
permits exactly pattern, category, and reason:

| Field | Contract |
| --- | --- |
| pattern | Required nonempty string in the v1 glob grammar below. It is a repository-relative policy pattern, not a filesystem path. |
| category | Required exact enum: generated, lockfile, vendored, or other. |
| reason | Required string whose outer Unicode whitespace is removed for storage and which is nonempty afterward. It is the human/audit explanation for omitting coverage. |

Unknown keys, non-string values, empty values, duplicate normalized patterns, invalid
glob syntax, unsupported glob features, and a reason that normalizes to empty reject
the complete project config in prepare. They do not disable a malformed rule or
default it to a different category. Rule order is part of the effective policy and
therefore enters the effective-config digest.

### 2. Path matching grammar

Matching uses normalized, case-sensitive, slash-separated repository paths from the
complete diff. It never consults the checkout filesystem, follows a symlink, or
normalizes a path through the operating system.

The v1 grammar is intentionally small:

| Form | Meaning |
| --- | --- |
| * | Matches zero or more non-slash characters in one path component. |
| ? | Matches exactly one non-slash character. |
| ** | Matches zero or more path components, including a zero-component match for forms such as a/**/b. |
| /name or /dir/** | A leading slash anchors the pattern to the repository root. The slash is syntax, not part of the matched path. |
| name | A pattern without a slash matches a file or directory component named name at any depth. Thus *.lock matches lockfile basenames anywhere. |
| dir/name or dir/** | A pattern containing a slash other than a trailing directory-form slash is anchored to the repository root. |
| **/name or **/dir/** | A leading **/ is the explicit any-depth form for a pattern that otherwise contains separators. |
| directory/ | A trailing slash only denotes directory form; it does not anchor the pattern. `vendor/` matches every file below any directory named vendor, while `/vendor/` matches only the root vendor directory and its descendants. |

The matcher accepts only these wildcard forms. It rejects a leading !, a NUL,
backslash escaping, bracket character classes, an absolute-drive spelling, an empty
component, and dot or dot-dot traversal components. All paths are interpreted from
the logical repository root, never through the checkout filesystem.

The separator rule is explicit: after an optional trailing directory-form slash is
removed, a leading or internal `/` anchors a pattern to the repository root; the
explicit `**/` prefix is the any-depth exception. Thus `dir/name` is root-anchored,
while `**/dir/name` can match at any depth. A terminal `/` is only directory-form
syntax, so `vendor/` remains unanchored and `/vendor/` is root-only.

Patterns are evaluated in listed order. Every positive match excludes the path, and
the last matching rule supplies the recorded rule index, pattern, category, and
reason. Negation and re-inclusion remain unsupported: a v1 implementation must
reject `!` rather than interpret it as a literal or silently ignore it. This lets a
later, more-specific category/reason supersede a broad earlier one without creating
a hidden inclusion path.

For each parsed diff block, the implementation considers every present side:

- an added file has its new path;
- a deleted file has its old path;
- a modified file normally has both paths;
- a rename has both old and new paths, even if the content diff is empty.

A block is excluded only when every present old/new path matches an exclusion. For a
rename, matching only the old path or only the new path leaves the entire block
reviewable. Different sides may match different exclusion rules; the provenance
records the effective match for each side. A parser failure or a block with no
reliable present path is a scope-integrity failure, never an implicitly excluded
file.

### 3. Complete source scope before local filtering

Prepare retains its current order of trust checks:

1. Resolve and bind the immutable PR/MR revision and the trusted policy according to
   SPEC-47.
2. Fetch the complete platform diff for that immutable revision and revalidate the
   revision afterwards.
3. Prove platform completeness using the existing GitHub raw-diff and GitLab
   paginated/raw-fallback rules.
4. Parse the complete response into lossless whole diff blocks and determine their
   trusted policy disposition.
5. Build the reviewable diff, enforce local limits on that reviewed scope, then copy
   the filtered repository snapshot and write the bundle.

Step 3 is non-negotiable. A GitLab collapsed, too_large, duplicate, or overflow
response, a GitHub oversized/raw-diff rejection, a pagination cap, a platform
retrieval error, or a moving revision fails exactly as it did before. The matcher may
not examine a partial platform response and decide that every visible path is
excludable. A known generated path is not evidence that omitted bytes contain no
reviewable path.

The diff-block parser must account for every source byte as a stable preamble or one
complete file block, preserve original ordering and bytes for included blocks, and
derive old/new paths from the same parsed representation used by anchoring. If it
cannot do so, prepare fails with incomplete_review_scope. It may not count a
diff --git-looking line in file content as a separate file, rewrite a hunk, or drop
unrecognized bytes.

inputs/mr.diff becomes the complete reviewed diff, not an unlabelled truncation of
the platform response. It contains each included block unchanged and no excluded
block. The full source diff is not copied into the reviewer bundle merely for audit:
the immutable revision plus source hash are sufficient to retrieve it for an
authorized audit, while retaining excluded raw content beside a secret-bearing
adapter would weaken the scope boundary.

The existing limits.max_diff_bytes and limits.max_files apply only after filtering:

- reviewed bytes are the exact UTF-8 byte length of inputs/mr.diff;
- reviewed files are the number of included parsed blocks;
- source bytes and source files remain provenance measurements, not alternate
  limit values;
- an over-limit reviewed scope fails prepare as incomplete_review_scope, with both
  source and reviewed measurements in its bounded diagnostic.

No source file is split or silently truncated. If a reviewable scope still exceeds a
limit, the operator must reduce the source change, use an explicit operational
exception outside this feature, or choose a different reviewed policy on a trusted
target branch.

### 4. Snapshot and provenance artifacts

The repository snapshot is built only after filtering and limit enforcement. Its
dynamic ignore predicate uses the same validated matcher and omits every repository
file that matches an exclusion, not merely changed matching files. That prevents an
adapter from discovering an excluded generated or lockfile through repo_snapshot
after it was removed from the prompt diff. Existing no-follow, regular-file, and
directory-containment protections remain mandatory.

The manifest is the authoritative scope record. It adds a strict, additive
review_scope object such as:

~~~json
{
  "schema_version": "review_scope.v1",
  "policy_sha256": "<canonical policy hash>",
  "source": {
    "diff_sha256": "<complete platform diff hash>",
    "bytes": 260732,
    "file_count": 84
  },
  "reviewed": {
    "diff_sha256": "<inputs/mr.diff hash>",
    "bytes": 164698,
    "file_count": 83
  },
  "reviewable": [
    {
      "old_path": "src/service.py",
      "new_path": "src/service.py",
      "source_block_sha256": "<sha256>",
      "source_bytes": 412
    }
  ],
  "excluded": [
    {
      "old_path": "generated/client.py",
      "new_path": "generated/client.py",
      "source_block_sha256": "<sha256>",
      "source_bytes": 95850,
      "matches": [
        {
          "side": "old",
          "path": "generated/client.py",
          "rule_index": 1,
          "pattern": "generated/**",
          "category": "generated",
          "reason": "Regenerated from the checked-in schema."
        },
        {
          "side": "new",
          "path": "generated/client.py",
          "rule_index": 1,
          "pattern": "generated/**",
          "category": "generated",
          "reason": "Regenerated from the checked-in schema."
        }
      ]
    }
  ],
  "snapshot": {
    "excluded_path_count": 1,
    "excluded_file_bytes": 1234,
    "repo_snapshot_sha256": "<sha256>"
  },
  "no_reviewable_changes": false
}
~~~

The real object records every included and excluded changed block, normalized
old/new paths, source block hash/bytes, and every effective side match. The example
uses illustrative counts only. The policy hash covers the ordered normalized
exclusions including category and reason; the expanded effective-config digest from
SPEC-47 covers that policy as well.

The artifact writer may never truncate an included/excluded path list or replace it
with a count. If the complete auditable record would exceed a trusted artifact bound,
prepare fails as incomplete_review_scope with the record size/count, rather than
silently losing coverage evidence. It must not include excluded raw diff text or
repository contents in this record.

Consensus, post, and gate propagate a compact scope identity containing at least the
review_scope schema version, policy hash, source/reviewed diff hashes and counts, and
no_reviewable_changes flag. Their schemas/types require it to match the manifest.
Reviewer and critique batches remain bound through effective_config_sha256, which
now includes the policy hash; a batch from a different scope cannot join consensus.

### 5. Findings, state, and no-reviewable-changes semantics

Only paths present in reviewable blocks participate in normal review semantics:

- reviewers receive only inputs/mr.diff and the filtered snapshot;
- finding finalization and anchor validation derive allowed old/new paths from the
  reviewed diff, so a finding on an excluded path cannot become a finalized finding;
- critique, consensus, and posting consume only finalized reviewable findings;
- new state records and comments can be created only for reviewable findings.

Absence-based resolution receives a reviewed-path proof from the manifest. A
persisted state record is eligible for absence-based resolution only when its
canonical anchor path is in the reviewable path set for this run. A record whose
anchor maps to an excluded old or new path, or to a path not reviewed in this run,
is carried forward unchanged. This rule applies even if the current filtered
consensus contains no finding for that path. Excluding a path is never evidence that
an older issue was fixed.

When every changed block is excluded, prepare emits a first-class
no-reviewable-changes input outcome:

- the manifest has no_reviewable_changes=true with the full source/exclusion
  provenance and an empty reviewed scope;
- it writes the resolved config and manifest but no repo_snapshot and no model-ready
  prompt/diff input;
- review and critique fan-out are skipped by template output/rules where possible,
  or by a trusted preflight that exits before prompt rendering, adapter spawn, or a
  provider request;
- consensus emits a schema-defined no_reviewable_changes outcome with zero findings
  and the compact scope identity;
- post emits skipped_no_reviewable_changes without creating a platform client,
  loading/planning state, posting, updating, resolving, or mutating any comment,
  and **exits zero** — it is the terminal stage, and a deliberate no-op is a
  successful publication outcome, not a failure.

> **Reconciled against SPEC-55.** This spec originally added a fourth bullet, a
> `passed_no_reviewable_changes` gate outcome with `block_merge=false`. There is
> no gate and no `block_merge`: Code Tribunal publishes review output and makes
> no merge decision. The no-reviewable outcome is carried entirely by
> `post_result.status` and the artifacts, which is where it always did the real
> work. Adding a status to `post_result.status` requires revisiting `post.py`'s
> exit mapping in the same change — an unrecognized status exits nonzero by
> design, so a new status added without that edit fails loudly rather than
> reporting a false success.

This is a successful no-op, not a failed panel, a reviewer skip hidden as a
normal clean review, or an empty consensus that can resolve every historical record.
It must be distinguishable in artifacts, job output, and operator diagnostics.

Because the policy is selected from the trusted target/base revision, a base-owned
exclusion is a standing green-gate channel: future head changes whose paths match it
receive no model review for that matched scope. When every changed block matches, the
intentional all-excluded no-reviewable-changes result means the head receives no model
review at all. Adding or changing a base-owned exclusion therefore requires
protected-branch/config-owner review as a coverage-policy change.

## Failure behavior

| Condition | Required result |
| --- | --- |
| review_scope is absent or exclusions is empty | Preserve the existing full-scope behavior, while recording the empty policy identity in new artifacts once the feature ships. |
| A rule is malformed, uses !, has an invalid category/reason, or has unsupported glob syntax | SPEC-47 project_config_invalid during prepare; do not ignore that rule. |
| Platform diff is incomplete, collapsed, too large, overflowing, paginated beyond its proof boundary, or cannot be fetched | Preserve the existing platform failure. Exclusions are not evaluated as a workaround. |
| Full diff cannot be parsed losslessly into paths/blocks, or scope provenance cannot be represented completely | Fail prepare as incomplete_review_scope; publish no usable bundle. |
| Reviewed bytes or files still exceed limits | Fail prepare as incomplete_review_scope with source/reviewed measurements; no model call. |
| Snapshot filtering encounters a containment/special-file/copy error | Fail prepare normally and publish no usable bundle. It must not copy the unfiltered snapshot. |
| A later artifact has a different policy hash, reviewed diff hash, or effective-config digest | Fail closed as scope/config integrity mismatch before consensus, post, or gate consequences. |
| All blocks are excluded | Produce the explicit passing no-reviewable-changes artifact chain; no snapshot, model call, state mutation, or comment mutation. |

An incomplete_review_scope result is a coverage failure, not a signal to continue
with the visible subset or retry consensus from a missing manifest. It exposes the
remaining reviewable scope and preserves the source-scope measurements needed for an
operator to decide whether to split the MR or use an independently documented
exception.

## Implementation seams

| Seam | Required change |
| --- | --- |
| SPEC-47 config loading and effective digest | Admit and validate the policy-owned review_scope object only after trusted source selection. Canonicalize its ordered rules and include them in the resolved/effective policy digest. |
| ai-review/src/ai_review/config.py | Add strict review_scope validation, a bounded pure matcher API, and no environment override. Do not use Python filesystem globbing or platform generated-file metadata. |
| ai-review/src/ai_review/input_bundle.py | Fetch/prove the complete source diff first; parse/filter whole blocks before _enforce_diff_limits, prompt data, state loading, and snapshot construction; emit the authoritative provenance or fail atomically. |
| anchors.py and diff parsing | Share a lossless old/new-path block representation with scope filtering and anchor validation so an excluded/unknown path cannot be reintroduced by a separate parser. |
| Snapshot copying | Extend the existing no-follow copier with a policy predicate applied to repository-relative paths, preserving containment and reporting scope-filter counts without copying excluded content. |
| prompt_render.py and adapter_runner.py | Treat inputs/mr.diff plus the filtered snapshot as the only reviewable model input. Recognize the explicit no-reviewable outcome before prompt generation or adapter/provider execution. |
| schema.py, types.py, and schemas | Add strict manifest review_scope, compact downstream scope identity, no-reviewable consensus/post statuses, and compatibility-safe artifact validation. Require policy/diff identity equality where artifacts meet. |
| consensus.py | Bind finalized batches to the scoped effective config, propagate scope provenance, and pass the reviewed-path proof into resolution eligibility. |
| post.py and state planning | Do not create or mutate state/comments for the no-reviewable outcome; prevent absence resolution for excluded or unreviewed record paths. |
| post.py exit mapping | Add the new status to the successful set in `post.py` in the same change that adds it to `PostStatus`; an unrecognized status exits nonzero by design. |
| GitHub and GitLab templates | Avoid model fan-out for the no-reviewable case when platform orchestration permits; otherwise use a trusted preflight. Keep prepare terminal and ensure `post` reports the explicit no-op rather than being skipped. |
| Reference, configuration, security, and operations docs | Document exact glob semantics, default empty policy, coverage artifacts, the no-op publication outcome, review/approval expectations, and the fact that a scope exclusion is not platform-truncation relief. |
| Images, templates, and supply chain | Release the matcher, schema/type changes, all stage handlers, and both templates in one immutable image/template revision. Do not allow a consumer to enable the new config on a mixed deployment where an older stage would treat the artifact as a normal empty review. |

## Tests

The implementation needs unit tests for the pure matcher, bundle tests for coverage
construction, schema/type tests, template tests, and controlled GitHub/GitLab
integration coverage. At minimum:

- Matching grammar: root-anchored and unanchored basenames, * and ?, ** including
  zero directories, trailing-directory forms, UTF-8/case-sensitive names, ordered
  overlapping matches, and rejection of !, traversal, backslashes, character
  classes, and malformed patterns. Explicit anchoring tests show that `dir/name`
  matches root `dir/name` but not `nested/dir/name`, while `**/dir/name` also
  matches the nested path, and that `vendor/` matches `vendor/file` and
  `nested/vendor/file` while `/vendor/` matches only `vendor/file`.
- Config contract: allowed categories, required nonblank reasons, unknown-key
  rejection, duplicate normalized patterns, empty default, and the proof that the
  policy arrives only through SPEC-47's target-revision config.
- Diff behavior: added, deleted, modified, binary-looking, empty-content, and renamed
  blocks; a rename with only one matching side remains entirely reviewable; a rename
  with both sides matching records both matches and is excluded.
- Limits: a source diff over the old limit succeeds only when the filtered reviewed
  bytes/files are within limits; a still-over-limit reviewed diff fails with
  incomplete_review_scope and never publishes a manifest/model-ready bundle.
- Platform completeness: existing GitHub oversized/raw-diff and GitLab
  collapsed/too_large/overflow/pagination failures remain failures even when every
  visible file matches a rule.
- Snapshot: matching changed and unchanged files are absent from repo_snapshot, a
  nonmatching file remains, no-follow containment still rejects hostile entries, and
  no excluded bytes appear in the review prompt or model-visible snapshot.
- Provenance: source/reviewed hashes, all block paths/bytes, side-specific
  rule/category/reason, policy hash, and compact downstream identity are deterministic,
  schema-valid, and mismatch-resistant. Oversized provenance fails rather than
  truncates.
- Findings and state: a model finding anchored to an excluded path cannot finalize;
  a prior state record on an excluded old/new path remains unchanged; reviewable-path
  resolution keeps its intended existing behavior.
- All-excluded flow: no snapshot, no prompt render, no adapter spawn/provider call,
  no state read/write plan, no comment mutation, zero normal findings, explicit
  consensus/post/gate statuses, and a passing gate carrying the reason.
- Template/DAG behavior: prepare failure stops downstream consumption; the all-excluded
  result remains a visible successful gate on both platforms; every later artifact
  rejects a different scope/effective-config identity.

## Acceptance criteria

- A trusted target-branch policy can exclude a whole changed file only through a
  validated pattern/category/nonblank reason, and every exclusion is inspectable in
  the manifest with paths, bytes, hashes, and effective side match metadata.
- A PR/MR head cannot add an exclusion that changes its own review. No environment,
  template variable, or generated-file flag offers an alternate exclusion path.
- The platform diff is proved complete before any exclusion. All prior
  truncated/oversized platform-response failures remain fail closed.
- Local limits apply to the deterministic reviewable diff after filtering. Any
  remaining over-limit scope fails explicitly as incomplete_review_scope rather than
  producing a partial review.
- Reviewers, anchors, consensus, state resolution, comments, and gate semantics see
  only reviewable paths. Existing state on excluded/unreviewed paths is never
  automatically resolved.
- An all-excluded change produces a first-class, auditable passing no-op: no snapshot,
  no model call, no state/comment mutation, and explicit consensus/post/gate results.
- A base-owned exclusion's standing green-gate behavior is documented and
  operator-visible: future head changes whose paths match it receive no model
  review for that matched scope, and an all-excluded head produces the explicit
  `no_reviewable_changes` no-op outcome above.
- Every producer/consumer schema and type validates the scope identity, and a
  mismatched config or scope artifact fails closed.
- GitHub and GitLab controlled runs demonstrate the same matching, provenance,
  no-op, and residual-limit behavior on the immutable published image/template pair.

## Rollout and compatibility

The default empty exclusions policy preserves current full-scope behavior. It does
not reinterpret historical artifacts: a new stage requiring scope provenance must
consume a freshly prepared bundle, and old images/templates must not be asked to
process a new no-reviewable status.

Roll out only after SPEC-47 is implemented and evidenced. Then land the matcher and
all producer/consumer/schema/template changes together, publish and attest the new
base and reviewer images, pin both supported templates to that same revision, and
run controlled GitHub/GitLab tests. Enable an adopter's first exclusion only after
the complete deployment is available. A policy change is a reduction in automated
coverage and requires protected-branch/config-owner review.

The operational response to an oversized source change remains separate: split
independently mergeable generated output from source changes where possible, or use
an audited manual/non-blocking exception under the consumer's process. This
specification neither hides a residual source-sized failure nor turns an incomplete
platform diff into a green result.

## Relationship to SPEC-47

SPEC-47 is the trust and binding boundary for this feature. It determines the
base-revision source, prevents adapter/credential substitutions, materializes the
policy in inputs/config.review.yaml, and requires every stage to validate the same
effective config. This specification adds only the coverage semantics inside that
trusted policy. Implementing the matcher first, or accepting exclusions from the
head/config environment before SPEC-47, would create an unauditable secret-bearing
scope bypass and is explicitly prohibited.
