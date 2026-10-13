# SPEC-48 — Auditable review-scope exclusions

- **Status:** Proposed (post-2.0.2; not a current product feature).
- **Severity:** Medium (availability). Nothing is silently omitted today: a diff over `limits.max_diff_bytes` or `limits.max_files` fails prepare, so a lockfile- or generated-file-dominated PR/MR gets no review at all rather than a false-clean one. The false-clean risk is what a careless implementation of this feature would introduce, and the contract below exists to prevent it.
- **Effort:** L. It touches input preparation, diff parsing, the snapshot copier, platform diff retrieval, finalization, consensus, state planning, posting, both templates, and two public artifact schemas.
- **ROI rank:** post-2.0.2, after SPEC-47.
- **Requires a major release:** new outcomes in `consensus.v2` and `post_result.v1`, which are public versioned artifacts. Ship it in the major release that re-lands S05. SPEC-66 ships earlier as compatible minor releases (optional fields on `consensus.v2`), so its health section already exists when this spec lands; this spec only adds a coverage line to it.
- **Depends on:**
  - [SPEC-47](spec-47-trusted-project-review-config.md): every stage verifies the effective-config digest. Exclusions are bound through that digest, so a job that sees a different exclusion policy fails before consequential work.
  - S05's model-location resolution (the reverted `anchors.resolve_location` from #171, 5845db9): needed to reject findings on excluded or off-diff paths.
  - [SPEC-66](spec-66-run-health-summary.md)'s health section: where the no-reviewable-changes and reduced-coverage outcomes become visible on the PR/MR.
- **Policy source:** the trusted configuration channel that already exists. The image's [`review.yaml`](../../ai-review/config/review.yaml) ships the rules, and an operator selects categories through a protected `AI_REVIEW_*` variable or a custom image's YAML. The reviewed repository never supplies exclusions.
- **Related work:** the existing complete-diff retrieval and snapshot-containment contracts; this specification preserves their fail-closed behavior, closes one gap in it (the GitLab pagination cap), and does not add a platform-generated-file shortcut.

## Rationale

A single generated artifact or lockfile can dominate an otherwise reviewable PR/MR and
push the whole diff past a local review limit. Raising the limit globally, trusting a
platform generated_file flag, or silently dropping the file is not an adequate
answer. Each either expands cost/exposure without a policy decision or hides which
changed code was not reviewed.

An adopter may reasonably decide that a checked-in generated directory, lockfile, or
vendored subtree has another review and validation path. That decision is still a
coverage reduction. It must be explicit, trusted, deterministic, visible in the
input and outcome artifacts, and incapable of being made by the reviewed repository
or the PR/MR head that receives secrets.

This specification adds a small, policy-configured exclusion language. It first
obtains and verifies the complete platform diff, then removes whole eligible diff
blocks before local limits, prompt construction, and snapshot copying. It reports
what was fetched, what was reviewed, what was excluded, and why. Remaining
reviewable scope still has the same limits and fails explicitly when too large.

The design deliberately treats an all-excluded change as a real outcome, not a green
absence of artifacts. That outcome makes no model call, creates no snapshot, does not
resolve existing state or touch discussion threads, and ends in a successful post
status that says why. Both that outcome and an ordinary reduced-scope run are
reported to the PR/MR through SPEC-66's run-health section of the summary comment,
not through a separate notice.

## Scope and non-goals

**In scope**

- A strict `review_scope` policy in `review_config.v3`: ordered rules shipped in the
  image's YAML, plus an operator category selector with one environment override.
- Repository-relative Gitignore-like matching with auditable rule/category/reason
  selection.
- Full platform-diff collection and completeness validation before filtering,
  including turning the GitLab pagination cap from a warning into a hard failure.
- Whole-diff-block filtering before local limits, prompt rendering, snapshot
  construction, and model execution.
- Input, consensus, and post provenance for complete source scope, reviewed scope,
  excluded paths, byte counts, policy, and no-reviewable-changes outcomes.
- Scope-aware finding validation and absence-based state resolution.
- A coverage line in SPEC-66's run-health section for reduced-scope and
  no-reviewable-changes runs.
- GitHub/GitLab templates, schemas/types, documentation, supply-chain rollout, and
  regression coverage.

**Out of scope**

- Any configuration source other than the loaded `AI_REVIEW_CONFIG` YAML and the
  one category-selector variable below. No file in the reviewed repository, at any
  revision, can add, enable, or change an exclusion; no platform metadata bit,
  custom-pattern variable, or workflow input can exclude scope. A repository-hosted
  policy file was rejected for SPEC-47 on 2026-10-13 and is not reintroduced here.
- Exclusions active by default. The shipped rules are inert until an operator
  selects their category, so upgrading never reduces coverage.
- Automatic classification of generated, lockfile, or vendored files outside the
  configured rules. Platform generated_file metadata may be diagnostic evidence but
  never policy.
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

`review_scope` is a new optional top-level object in `review_config.v3`. It is read
from the same file every stage already loads (`AI_REVIEW_CONFIG`, the image's
`/opt/ai-review/config/review.yaml` in the canonical templates). Adding an optional
key with an inert default does not change the schema version. It has two keys:

~~~yaml
review_scope:
  enabled_categories: []   # Override: AI_REVIEW_SCOPE_EXCLUSIONS
  exclusions:
    - pattern: "package-lock.json"
      category: lockfile
      reason: "Generated by npm; dependency changes are reviewed in package.json."
    - pattern: "/vendor/"
      category: vendored
      reason: "Vendored third-party source with its own upstream review."
~~~

| Field | Contract |
| --- | --- |
| enabled_categories | Optional list of distinct category names; default `[]`. Only rules whose category is listed are active. |
| exclusions | Optional ordered array of rules; default `[]`. Each rule permits exactly pattern, category, and reason. |
| exclusions[].pattern | Required nonempty string in the v1 glob grammar below. It is a repository-relative policy pattern, not a filesystem path. |
| exclusions[].category | Required exact enum: generated, lockfile, vendored, or other. |
| exclusions[].reason | Required string whose outer Unicode whitespace is removed for storage and which is nonempty afterward. It is the human/audit explanation for omitting coverage. |

**Shipped rules.** The image's `review.yaml` ships a short, conservative rule list
with `enabled_categories: []`. The proposal, which the implementing pull request
finalizes:

- **lockfile:** `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`,
  `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Cargo.lock`,
  `Gemfile.lock`, `composer.lock`, `go.sum`;
- **generated:** `*.min.js`, `*.min.css`, `*.pb.go`, `*_pb2.py`, `*_pb2.pyi`;
- **vendored:** `/vendor/` (root only; an unanchored `vendor/` would also match
  application directories named `vendor`).

No rule ships with category `other`; it exists for custom-image rules.

**Operator selection.** `AI_REVIEW_SCOPE_EXCLUSIONS` overrides
`enabled_categories`. It is parsed like `AI_REVIEW_REVIEWERS` in
`config.apply_env_overrides`:

- unset or blank: keep the YAML value;
- otherwise a comma-separated list of category names, whitespace-trimmed. Duplicate
  or unknown names are a `ConfigError`;
- the exact value `none` selects no category. This lets an operator turn off a
  custom image's enabled default without editing YAML.

It joins the closed list of supported policy overrides in the templates and
`docs/configuration.md`, with the same requirement as every other override: set it
at project/group (GitLab) or repository/workflow (GitHub) scope so that every job
sees it.

**Why a category selector and not a pattern variable.** Every existing `AI_REVIEW_*`
override is a scalar or a comma-separated list of known names; none carries
structured data. A rule needs a pattern, a category, and a reason, and putting that
in a CI variable would make an unreviewed, unversioned coverage policy in a
settings page. The variable therefore only chooses among rules that are already in
reviewed YAML. Custom patterns (category `other`, or project-specific generated
paths) are supported only by a custom image whose YAML adds rules. That is enough
for v1. A pattern variable needs its own decision.

**Validation.** `validate_config` checks `review_scope` in every stage, because every
stage calls `load_config`. Unknown keys, non-string values, empty values, duplicate
normalized patterns, invalid glob syntax, unsupported glob features, a reason that
normalizes to empty, and an `enabled_categories` entry that is not a known category
reject the configuration with `ConfigError`. A malformed rule is never disabled or
assigned a different category. A category that is enabled but has no rules is valid
and excludes nothing.

**Binding.** `effective_config_summary` gains a `review_scope` entry: the enabled
categories and the ordered active rules (pattern, category, reason). The effective
policy therefore enters `effective_config_sha256`, and SPEC-47's per-stage check
makes a job that sees a different selector or different rules fail before
consequential work. Rule order is part of the effective policy.

**Who can set it.** The rules come from the image or a custom image; the selector
comes from a protected CI variable. Both are the operator channel that already
controls models and the roster. A PR/MR author who can rewrite the CI definition can
set any variable; that boundary is [SPEC-43](spec-43-in-pipeline-trusted-image.md)'s,
not this spec's.

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

1. Load and validate the trusted configuration, including `review_scope`, and
   resolve and bind the immutable PR/MR revision.
2. Fetch the complete platform diff for that immutable revision and revalidate the
   revision afterwards.
3. Prove platform completeness using the existing GitHub raw-diff and GitLab
   paginated/raw-fallback rules.
4. Parse the complete response into lossless whole diff blocks and determine their
   trusted policy disposition.
5. Build the reviewable diff, enforce local limits on that reviewed scope, then copy
   the filtered repository snapshot and write the bundle.

Step 3 is non-negotiable. A GitLab collapsed, too_large, duplicate, or overflow
response, a GitHub oversized/raw-diff rejection, a platform retrieval error, or a
moving revision fails exactly as it does today. The matcher may not examine a
partial platform response and decide that every visible path is excludable. A known
generated path is not evidence that omitted bytes contain no reviewable path.

The GitLab pagination cap does not fail today, and this spec must change that. The
shared `_get_all_pages` helper in
[`platform/gitlab.py`](../../ai-review/src/ai_review/platform/gitlab.py) stops after
100 pages, writes a "pagination cap reached" warning to stderr, and returns the
truncated list (asserted by `test_gitlab_client.py` for thread listing). For
`fetch_diff`, the only backstop today is that a 10,000-entry truncated list still
exceeds `limits.max_files`. Filtering removes that backstop: a huge MR whose visible
entries all match an exclusion could pass on a truncated list. Before exclusions can
be enabled, a pagination cap reached while fetching MR diffs must raise a
`ReviewPlatformError`, the same as an overflow. Thread and note listing may keep
their current behavior; that is a separate decision.

Whole-block filtering must use each platform's most faithful file representation:

- **GitHub** returns one raw unified diff from the comparison endpoint. The block
  parser splits that text.
- **GitLab** returns a structured change list (`old_path`, `new_path`, `new_file`,
  `deleted_file`, `renamed_file`, `diff`), and `fetch_diff` then synthesizes text with
  `diff --git a/<old> b/<new>`, `--- a/<old>`, and `+++ b/<new>` headers for every
  entry. The synthesized headers never use `/dev/null`, so added/deleted status is
  not recoverable from the text. GitLab filtering therefore runs on the structured
  change list before text synthesis, and the reviewed `inputs/mr.diff` is synthesized
  only from included entries. Re-parsing the synthesized text is not an acceptable
  substitute.

The diff-block parser must account for every source byte as a stable preamble or one
complete file block, preserve original ordering and bytes for included blocks, and
derive old/new paths from the same parsed representation used by anchoring. If it
cannot do so, prepare fails with incomplete_review_scope. It may not count a
diff --git-looking line in file content as a separate file, rewrite a hunk, or drop
unrecognized bytes.

The existing `anchors.parse_unified_diff` is not that parser. By its own contract it
keeps only paths, hunk headers, and numbered lines; it drops file metadata, binary
patch sections, and no-newline markers, and `_parse_diff_paths` splits the
`diff --git` header on whitespace, so a path containing a space is misread.
`_enforce_diff_limits` in
[`input_bundle.py`](../../ai-review/src/ai_review/input_bundle.py) counts files by
`diff --git ` line prefixes. The lossless block representation replaces both uses,
and anchoring derives its paths from it.

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
exception outside this feature, or enable a different category or custom-image rule
through the operator channel.

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
active exclusions including category and reason, plus the enabled categories; the
`review_scope` entry in the effective-config summary covers the same policy, so
`effective_config_sha256` binds it as well.

The artifact writer may never truncate an included/excluded path list or replace it
with a count. If the complete auditable record would exceed a trusted artifact bound,
prepare fails as incomplete_review_scope with the record size/count, rather than
silently losing coverage evidence. It must not include excluded raw diff text or
repository contents in this record.

Consensus and post propagate a compact scope identity containing at least the
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

Today's finalization does not provide the second bullet. `finalize_finding_batch` in
[`schema.py`](../../ai-review/src/ai_review/schema.py) normalizes the model's
old/new paths and computes a context hash from `inputs/mr.diff`, but it does not
reject an anchor whose path is absent from that diff. S05's
`anchors.resolve_location` (#171, 5845db9; reverted for 2.0.2) maps a model-authored
location onto the prepared diff and fails when it does not resolve to exactly one
contiguous range. That location resolution is a prerequisite: with it, a finding on
an excluded or otherwise off-diff path fails resolution because the excluded block is
absent from the reviewed diff. This spec must not add a second, path-allowlist check
beside it.

Absence-based resolution receives a reviewed-path proof from the manifest. A
persisted state record is eligible for absence-based resolution only when its
canonical anchor path is in the reviewable path set for this run. A record whose
anchor maps to an excluded old or new path, or to a path not reviewed in this run,
is carried forward unchanged. This rule applies even if the current filtered
consensus contains no finding for that path. Excluding a path is never evidence that
an older issue was fixed.

The guard belongs in `state_plan._plan_stale_records` in
[`state_plan.py`](../../ai-review/src/ai_review/state_plan.py). Today that function
moves every unmatched `open` record to `resolved` when the resolution quorum holds,
or to `stale_unverified` when it does not, regardless of the record's path. The
reviewed-path proof adds one condition before that transition; human `/ai-review`
commands keep their current effect.

When every changed block is excluded, prepare emits a first-class
no-reviewable-changes input outcome:

- the manifest has no_reviewable_changes=true with the full source/exclusion
  provenance and an empty reviewed scope;
- it writes the resolved config and manifest but no repo_snapshot and no model-ready
  prompt/diff input;
- review and critique adapters exit through a trusted preflight in
  [`adapter_runner.py`](../../ai-review/src/ai_review/adapter_runner.py) that reads
  the bound manifest and writes a digest-bound `skipped` result before prompt
  rendering, adapter spawn, or a provider request. GitHub may additionally skip the
  jobs through a prepare job output. GitLab cannot: `rules:` are evaluated when the
  pipeline is created, before prepare has run, so the review and critique jobs always
  start and the preflight is the only skip mechanism there;
- consensus emits a schema-defined no_reviewable_changes outcome with zero findings
  and the compact scope identity, rather than treating every seat's `skipped` result
  as a failed panel;
- post emits skipped_no_reviewable_changes and **exits zero**. It does not load or
  plan state and does not create, update, or resolve any discussion thread. Its only
  platform write is the summary comment carrying SPEC-66's run-health section, which
  reports the no-reviewable-changes outcome and the excluded-scope counts. `post` is
  the terminal stage, and a deliberate no-op is a successful publication outcome, not
  a failure.

There is no gate stage and no merge verdict (SPEC-55): the outcome is carried by the
consensus and post statuses, the artifacts, and the run-health section. Adding the
status to `PostStatus` requires adding it to `SUCCESSFUL_POST_STATUSES` in
[`post.py`](../../ai-review/src/ai_review/post.py) in the same change. An
unrecognized status exits nonzero by design, so a status added without that edit
fails loudly rather than reporting a false success.

This is a successful no-op, not a failed panel, a reviewer skip hidden as a
normal clean review, or an empty consensus that can resolve every historical record.
It must be distinguishable in artifacts, job output, operator diagnostics, and the
SPEC-66 health grade.

A run in which some blocks were excluded and some were reviewed is an ordinary
review, but it is reduced coverage. SPEC-66's run-health section reports it with the
excluded block count and categories, so a reader of the PR/MR can tell that the
review did not cover every changed file. That line lives in SPEC-66's existing
section; this spec does not add a separate notice, comment, or artifact family.

An enabled category is a standing coverage-reduction channel: every later head
change whose paths match it gets no model review for that scope. When every changed
block matches, the intentional all-excluded no-reviewable-changes result means the
head gets no model review at all. A contributor who knows the policy can put a change
in a matching path, for example by editing a lockfile or a file under `/vendor/`,
and it will not be model-reviewed. Enabling a category or adding a custom-image rule
is therefore a coverage-policy decision for whoever controls the protected variables
or the image, and the configuration docs say so.

## Failure behavior

| Condition | Required result |
| --- | --- |
| review_scope is absent or exclusions is empty | Preserve the existing full-scope behavior, while recording the empty policy identity in new artifacts once the feature ships. |
| A rule is malformed, uses !, has an invalid category/reason, or has unsupported glob syntax | `ConfigError` from `validate_config` in every stage that loads the config; do not ignore that rule. |
| `AI_REVIEW_SCOPE_EXCLUSIONS` names an unknown or duplicate category | `ConfigError`; do not fall back to the YAML value. |
| A later job sees a different selector or rule list than prepare | SPEC-47's effective-config mismatch, before a provider call, consensus decision, or platform client. |
| Platform diff is incomplete, collapsed, too large, overflowing, or cannot be fetched | Preserve the existing platform failure. Exclusions are not evaluated as a workaround. |
| GitLab MR diff pagination reaches the page cap | Fail prepare with a platform error (new; today this warns and truncates). Exclusions are not evaluated. |
| Full diff cannot be parsed losslessly into paths/blocks, or scope provenance cannot be represented completely | Fail prepare as incomplete_review_scope; publish no usable bundle. |
| Reviewed bytes or files still exceed limits | Fail prepare as incomplete_review_scope with source/reviewed measurements; no model call. |
| Snapshot filtering encounters a containment/special-file/copy error | Fail prepare normally and publish no usable bundle. It must not copy the unfiltered snapshot. |
| A later artifact has a different policy hash, reviewed diff hash, or effective-config digest | Fail closed as scope/config integrity mismatch before consensus or post consequences. |
| All blocks are excluded | Produce the explicit successful no-reviewable-changes artifact chain; no snapshot, model call, state mutation, or thread mutation. The SPEC-66 summary health section reports it. |

An incomplete_review_scope result is a coverage failure, not a signal to continue
with the visible subset or retry consensus from a missing manifest. It exposes the
remaining reviewable scope and preserves the source-scope measurements needed for an
operator to decide whether to split the MR or use an independently documented
exception.

## Implementation seams

| Seam | Required change |
| --- | --- |
| Effective-config binding | Add the `review_scope` entry (enabled categories, ordered active rules) to `effective_config_summary`. Rely on SPEC-47's checks in review, critique, consensus, and post; add no new binding mechanism. |
| [`review.yaml`](../../ai-review/config/review.yaml) and [`configuration.md`](../configuration.md) | Ship the rule list with `enabled_categories: []`. Document the categories, the shipped patterns, `AI_REVIEW_SCOPE_EXCLUSIONS`, custom-image rules, and the coverage-reduction warning. |
| [`config.py`](../../ai-review/src/ai_review/config.py) | Add `review_scope` to `TOP_LEVEL_KEYS` with strict validation, a bounded pure matcher API, and the `AI_REVIEW_SCOPE_EXCLUSIONS` category selector in `apply_env_overrides` (parsed like `apply_reviewer_roster`). No pattern-carrying variable. Do not use Python filesystem globbing or platform generated-file metadata. |
| [`platform/gitlab.py`](../../ai-review/src/ai_review/platform/gitlab.py) | Make a pagination cap reached by `fetch_diff` a `ReviewPlatformError` instead of a stderr warning. Expose the complete, completeness-checked structured change list so filtering runs before text synthesis. |
| [`platform/github.py`](../../ai-review/src/ai_review/platform/github.py) | No retrieval change. The raw comparison diff and its HTTP 406 oversized rejection remain the completeness boundary. |
| [`input_bundle.py`](../../ai-review/src/ai_review/input_bundle.py) | Fetch/prove the complete source diff first; filter whole blocks (GitLab: structured entries; GitHub: parsed raw blocks) before `_enforce_diff_limits`, prompt data, state loading, and snapshot construction; count reviewed files from blocks, not `diff --git ` prefixes; emit the authoritative provenance or fail atomically. |
| [`anchors.py`](../../ai-review/src/ai_review/anchors.py) and diff parsing | Replace the lossy `parse_unified_diff`/`_parse_diff_paths` path extraction with one lossless old/new-path block representation shared by scope filtering, limits, and S05's `resolve_location`, so an excluded or unknown path cannot be reintroduced by a separate parser. |
| [`schema.py`](../../ai-review/src/ai_review/schema.py) finalization | Depend on S05's location resolution against the reviewed diff; a finding whose location does not resolve, including one on an excluded path, is dropped and counted. No separate path allowlist. |
| Snapshot copying | Extend the existing no-follow copier with a policy predicate applied to repository-relative paths, preserving containment and reporting scope-filter counts without copying excluded content. |
| [`adapter_runner.py`](../../ai-review/src/ai_review/adapter_runner.py) and [`prompt_render.py`](../../ai-review/src/ai_review/prompt_render.py) | Treat inputs/mr.diff plus the filtered snapshot as the only reviewable model input. Add the trusted no-reviewable preflight that writes a digest-bound `skipped` result before prompt generation or adapter/provider execution. |
| [`types.py`](../../ai-review/src/ai_review/types.py) and [schemas](../../ai-review/schemas/) | Add strict manifest review_scope, compact downstream scope identity, the no-reviewable `consensus.schema.json` outcome and `post_result.schema.json` status, and compatibility-safe artifact validation. Require policy/diff identity equality where artifacts meet. These are public versioned artifacts; the version step is the major release's decision. |
| [`consensus.py`](../../ai-review/src/ai_review/consensus.py) | Bind finalized batches to the scoped effective config, propagate scope provenance, emit the no-reviewable outcome instead of a failed panel, and pass the reviewed-path proof into resolution eligibility. |
| [`state_plan.py`](../../ai-review/src/ai_review/state_plan.py) | In `_plan_stale_records`, carry forward unchanged any unmatched record whose anchor path is excluded or not in the reviewed path set. |
| [`posting.py`](../../ai-review/src/ai_review/posting.py) and [`post.py`](../../ai-review/src/ai_review/post.py) | For the no-reviewable outcome, skip state loading/planning and thread mutation and publish only the summary with SPEC-66's health section. Add the new status to `PostStatus` and `SUCCESSFUL_POST_STATUSES` in the same change. |
| SPEC-66 run-health section | Add the coverage line: reviewed/excluded block counts and categories for reduced-scope runs, and the no-reviewable-changes grade and reason. No parallel notice. |
| GitHub and GitLab templates | GitHub may skip review/critique fan-out from a prepare output; GitLab relies on the adapter preflight because `rules:` cannot see prepare results. Keep prepare terminal on failure and ensure `post` runs and reports the explicit no-op rather than being skipped. Add `AI_REVIEW_SCOPE_EXCLUSIONS` to the documented list of supported overrides. |
| Reference, configuration, security, and operations docs | Document exact glob semantics, default empty policy, coverage artifacts, the no-op publication outcome, review/approval expectations, and the fact that a scope exclusion is not platform-truncation relief. |
| Images, templates, and supply chain | Release the matcher, schema/type changes, all stage handlers, and both templates in one immutable image/template revision, per the release process in effect. Do not allow a consumer to enable the new config on a mixed deployment where an older stage would treat the artifact as a normal empty review. |

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
  rejection, duplicate normalized patterns, the shipped rules inert under the default
  `enabled_categories: []`, and the shipped `review.yaml` passing validation.
- Selector: `AI_REVIEW_SCOPE_EXCLUSIONS` unset/blank keeps the YAML value, a list
  enables exactly those categories, `none` enables none, and unknown or duplicate
  names fail (in
  [`test_config_env_overrides.py`](../../ai-review/tests/unit/test_config_env_overrides.py)).
  A selector visible to prepare but not to a review or post job fails SPEC-47's
  binding check.
- Source isolation: a `review.yaml` or any other file in the reviewed checkout or
  snapshot has no effect on the active rules.
- Diff behavior: added, deleted, modified, binary-looking, empty-content, and renamed
  blocks; a rename with only one matching side remains entirely reviewable; a rename
  with both sides matching records both matches and is excluded.
- Limits: a source diff over the old limit succeeds only when the filtered reviewed
  bytes/files are within limits; a still-over-limit reviewed diff fails with
  incomplete_review_scope and never publishes a manifest/model-ready bundle.
- Platform completeness: existing GitHub oversized/raw-diff and GitLab
  collapsed/too_large/overflow failures remain failures even when every visible file
  matches a rule. A GitLab MR diff that reaches the pagination cap now fails prepare
  even when every returned entry matches a rule.
- GitLab structured filtering: added, deleted, and renamed entries are classified from
  `new_file`/`deleted_file`/`renamed_file` and old/new paths, not from synthesized
  headers; the reviewed `inputs/mr.diff` contains only included entries. Paths with
  spaces are matched and counted correctly on both platforms.
- Snapshot: matching changed and unchanged files are absent from repo_snapshot, a
  nonmatching file remains, no-follow containment still rejects hostile entries, and
  no excluded bytes appear in the review prompt or model-visible snapshot.
- Provenance: source/reviewed hashes, all block paths/bytes, side-specific
  rule/category/reason, policy hash, and compact downstream identity are deterministic,
  schema-valid, and mismatch-resistant. Oversized provenance fails rather than
  truncates.
- Findings and state: a model finding located on an excluded path fails S05 location
  resolution and cannot finalize; a prior state record on an excluded old/new path
  remains unchanged through `_plan_stale_records`; reviewable-path resolution keeps
  its intended existing behavior.
- All-excluded flow: no snapshot, no prompt render, no adapter spawn/provider call,
  no state read/write plan, no thread mutation, zero normal findings, explicit
  consensus/post statuses, post exit zero, and a SPEC-66 health section carrying the
  reason.
- Reduced-scope flow: a run with some excluded blocks reports the excluded count and
  categories in the SPEC-66 health section.
- Template/DAG behavior: prepare failure stops downstream consumption; on GitLab the
  review and critique jobs start and exit through the preflight without a provider
  call; the all-excluded result is a visible successful post on both platforms; every
  later artifact rejects a different scope/effective-config identity.

## Acceptance criteria

- A whole changed file can be excluded only by an active rule (a validated
  pattern/category/nonblank reason from the loaded YAML whose category is enabled),
  and every exclusion is inspectable in the manifest with paths, bytes, hashes, and
  effective side match metadata.
- Nothing in the reviewed repository can add or enable an exclusion. The only
  operator inputs are the YAML in the image and the category selector variable; no
  pattern variable, template input, or generated-file flag offers an alternate path.
- With no operator action, an upgrade excludes nothing.
- The platform diff is proved complete before any exclusion. All prior
  truncated/oversized platform-response failures remain fail closed, and the GitLab
  MR diff pagination cap is a hard failure.
- Local limits apply to the deterministic reviewable diff after filtering. Any
  remaining over-limit scope fails explicitly as incomplete_review_scope rather than
  producing a partial review.
- Reviewers, anchors, consensus, state resolution, and comments see only reviewable
  paths. Existing state on excluded/unreviewed paths is never automatically resolved.
- An all-excluded change produces a first-class, auditable successful no-op: no
  snapshot, no model call, no state/thread mutation, explicit consensus/post results,
  and a SPEC-66 health section that names the outcome.
- A reduced-scope run is visible on the PR/MR through the SPEC-66 health section, not
  only in artifacts.
- An enabled category's standing coverage-reduction behavior is documented and
  operator-visible: future head changes whose paths match it receive no model
  review for that matched scope, and an all-excluded head produces the explicit
  `no_reviewable_changes` no-op outcome above.
- Every producer/consumer schema and type validates the scope identity, and a
  mismatched config or scope artifact fails closed.
- GitHub and GitLab controlled runs demonstrate the same matching, provenance,
  no-op, and residual-limit behavior on the immutable published image/template pair.

## Rollout and compatibility

The default `enabled_categories: []` preserves current full-scope behavior even
though the image ships rules. It does
not reinterpret historical artifacts: a new stage requiring scope provenance must
consume a freshly prepared bundle, and old images/templates must not be asked to
process a new no-reviewable status.

The new consensus outcome and post status change `consensus.v2` and
`post_result.v1`, which are public versioned artifacts under the
[compatibility boundary](../development/architecture.md#compatibility-boundary). This
spec therefore ships only in a major release. The intended vehicle is the major
release that re-lands S05 (and SPEC-45), so that those artifacts take one version
step rather than several. SPEC-66 ships before it as compatible minor releases, so
this spec extends an existing health section rather than introducing one.

The order is:

1. SPEC-47 (asset lookup and per-stage binding), in 2.0.3 or another compatible
   release.
2. SPEC-66's health section, in compatible minor releases.
3. S05's location resolution re-landed in the major release, so finalization
   resolves model locations against the prepared diff.
4. SPEC-48, in the same major release.

The S01 release migration is independent; keep the major release out of the same
cycle as that migration. Then land the matcher and all producer/consumer/schema/
template changes together, publish and pin the new base and reviewer images per the
release process in effect, pin both supported templates to that same revision, and
run controlled GitHub/GitLab tests. An operator should set
`AI_REVIEW_SCOPE_EXCLUSIONS` only after every job in the pipeline runs the new image
pair. An older image ignores the unknown variable and would review full scope, while
a newer prepare would record a scoped manifest, so SPEC-47's binding check rejects a
mixed deployment rather than silently mixing scopes. Enabling a category is a
reduction in automated coverage and needs the same owner approval as any other
protected-variable change.

The operational response to an oversized source change remains separate: split
independently mergeable generated output from source changes where possible, or use
an audited manual/non-blocking exception under the consumer's process. This
specification neither hides a residual source-sized failure nor turns an incomplete
platform diff into a green result.

## Relationship to SPEC-47

This spec needs only the part of SPEC-47 that remains: every stage verifies the
effective-config digest before consequential work, and runtime assets come from the
trusted root. The exclusion policy lives in the existing trusted configuration (the
image's YAML plus one protected selector variable), so it needs no new configuration
source, no platform file reads, and no ADR. SPEC-47's repository-hosted policy file
was rejected on 2026-10-13; this spec must not reintroduce it as a way to deliver
exclusions. Shipping the matcher before SPEC-47's per-stage binding would let a
review or post job run under a different exclusion policy than prepare recorded,
and is prohibited.
