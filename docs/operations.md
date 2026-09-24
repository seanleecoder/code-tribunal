# Operations

## Upgrade from 1.0.x to 2.0

2.0 removes the merge gate and retires the 1.x configuration contract. Code
Tribunal now only publishes review output; it never decides whether a change may
merge. Upgrade the template, both images, variables, and any custom
configuration together, in this order.

1. **Save the rollback set:** the current workflow or template SHA, both image
   digests, custom `review.yaml`, branch-protection or ruleset entries, and
   CI variables.
2. **Remove the gate first.** On GitHub, delete any branch-protection or
   ruleset entry that requires the `gate` check: 2.0 has no such job, and a
   required check that never reports leaves pull requests permanently
   unmergeable. On GitLab, remove custom jobs, `needs`, or rules that reference
   `ai_review_gate`; the name stays reserved, so a consumer job cannot take it.
3. **Delete retired variables.** Config load rejects each of these with a
   migration message rather than ignoring it:
   `AI_REVIEW_CLAUDE_ENABLED`, `AI_REVIEW_CODEX_ENABLED`,
   `AI_REVIEW_OPENCODE_ENABLED`, `AI_REVIEW_CURSOR_ENABLED`,
   `AI_REVIEW_MERGE_GATE_ENABLED`, and `AI_REVIEW_STATE_BACKEND`. On GitLab,
   project and group variables reach every job, so a leftover value fails the
   pipeline. On GitHub the canonical workflow no longer forwards them, but a
   hand-edited copy that still does will fail.
4. **Select the panel with `AI_REVIEW_REVIEWERS`.** It must name at least three
   seats; 1.0.x accepted two. The default remains Claude, Codex, and OpenCode.
   On GitHub, Cursor receives `CURSOR_API_KEY` only when the repository variable
   `AI_REVIEW_REVIEWERS` names `cursor`, so enabling Cursor in YAML alone
   leaves it without a credential.
5. **Migrate custom configuration to `review_config.v3`.** Every 1.x release
   shipped `review_config.v1`, which is rejected once with the complete list of
   removed keys. Delete the keys listed in
   [the 1.x migration summary](configuration.md#1x-migration-summary) and set
   `schema_version: review_config.v3`.
6. **Update the template and both images together:** the complete canonical
   GitHub workflow, or the protected GitLab template SHA with its base image,
   reviewer image, and trusted source SHA.
7. **Update GitLab `needs`.** Review and critique are now one parallel matrix
   job apiece, `AI review` and `AI critique`. The expanded names such as
   `AI review: [claude]` remain, but a custom job that needed one of them must
   now use `needs:parallel:matrix` against the matrix job.
8. **Update artifact consumers.** Tools that read `consensus.json` must accept
   `consensus.v2`: independent support and informational decisions replace the
   removed blocking and vote-count fields. There is no `out/gate/` artifact.
9. **Expect one cosmetic thread update.** Existing threads keep their hidden
   marker and are refreshed once from `render-body.v3` to `render-body.v4`,
   which uses a `Support:` footer. Identities are updated, not duplicated.
10. **Verify:** open a test change, then confirm posting, state, and commands.
    `post` is the terminal job. It fails only on a publication failure, never
    because of a finding's severity.

To roll back, restore the saved workflow or template SHA with its matching
images, variables, and configuration, and start again at prepare. Threads receive
one reverse cosmetic update back to `render-body.v3`. Re-add the required gate
check only if you restore a 1.0.x template.

## Upgrade from 0.4.x to 1.0

This section is retained for installations still on 0.4.x. Complete it, then
apply [the 1.0.x to 2.0 upgrade](#upgrade-from-10x-to-20).

Treat the upgrade as a coordinated template, image, configuration, schema, and
state migration.

1. Save the current template SHA, image digests, custom configuration, required
   check/settings, and bot credential identities for rollback.
2. Remove retired top-level and nested placeholders. In particular, remove
   `critique.max_rounds`, split GitLab token variables, obsolete retention
   overflow fields, and removed `respond`/artifact enum values.
3. Rename `keep_resolved_runs` and `keep_stale_runs` to
   `keep_resolved_records` and `keep_stale_records`. Replace legacy
   `state.overflow_behavior: fail_closed` with the explicit
   `state.fail_closed_on_load_error` choice. For enforcing installs prefer
   `state.fail_closed_on_load_error: true`.
4. Configure one protected `GITLAB_TOKEN`. Verify the resolution threshold
   against the enabled reviewer count, and that at least three seats are
   enabled. Do not set
   `AI_REVIEW_LOCAL_MOCK` or `AI_REVIEW_ALLOW_LOCAL_MOCK` on the consumer
   project.
5. Update the complete canonical workflow or the protected GitLab template SHA;
   keep base image, reviewer image, and trusted source SHA together.
6. Start a fresh run at prepare. Old finding/critique batches lack required
   quality/digest fields and must not be reused. Expanded effective-config
   hashing also invalidates old prepare manifests.
7. Expect a one-time update of existing bot-authored bodies when the render-body
   version changes. This should update existing identities, not duplicate them.
8. Verify state ownership, posting, and commands.
9. Leave Cursor off unless you deliberately accept its separate egress path. It
   is a supported peer seat; enabling it requires `CURSOR_API_KEY`.

Consumers upgrading from a pre-0.3.1 GitLab template must also update custom
`needs`, overrides, dashboards, and scripts that refer to the old job names:

| Previous job | Current grouped job |
|---|---|
| `review_claude` | `AI review: [claude]` |
| `review_codex` | `AI review: [codex]` |
| `review_opencode` | `AI review: [opencode]` |
| `critique_claude` | `AI critique: [claude]` |
| `critique_codex` | `AI critique: [codex]` |
| `critique_opencode` | `AI critique: [opencode]` |

Cursor review/critique jobs are new optional grouped jobs and have no legacy
identifier.

Python-package consumers must move to the supported containers and CI templates.
There is no supported installable Python distribution.

## Failure behavior

| Failure class | Behavior | Operator action |
|---|---|---|
| One reviewer/provider fails | Panel degrades; other trustworthy evidence may proceed | Inspect `out/status/`; retry or fix credential/model |
| All findings from a seat are malformed | Seat is not resolution-eligible or operationally successful | Fix model/schema compatibility; do not lower thresholds reflexively |
| Critique disabled or optional evidence absent | Consensus uses valid reviewer evidence without critique | Confirm this matches rollout policy |
| No usable reviewer succeeds | Consensus exits 3; no posting decision | Restore provider/adapter availability |
| Run/config/artifact identity mismatch | Consensus exits 3 before combining evidence | Rerun from prepare with identical project-scoped overrides |
| State load fails and `fail_closed_on_load_error=false` | Prepare warns and begins from empty recoverable state | Investigate ownership/API/checksum; expect conservative repost risk |
| State load fails and option is true | Prepare fails | Restore state/API access or make a deliberate policy change |
| Post is `failed`, `partial_failed`, or `state_overflow` | `post` exits nonzero and the job fails | Repair API/state capacity and rerun post |
| Head changes before posting | No stale mutation; `post` reports `stale_head` and exits 0 | Let the newer revision's run own the review |
| Consensus surfaces a `blocker` finding | A thread is posted; `post` exits 0 | Treat it as review input. Merge policy is the repository's, not Code Tribunal's |
| External fork lacks protected secrets | Canonical GitHub flow skips; GitLab topology withholds/fails safely | Use maintainer-controlled trusted review, never expose secrets to fork code |

“Fail closed” is therefore failure-class specific. Reviewer-seat loss can
degrade open; artifact integrity and post/state loss fail closed; stale-head
handling intentionally performs no mutation and yields to the newer run.
Findings are not a failure class at all: severity is an impact label, and no
finding causes a nonzero exit anywhere in the pipeline.
The precedence and exit behavior are exercised by
[`test_post.py`](../ai-review/tests/unit/test_post.py),
[`test_consensus_integrity.py`](../ai-review/tests/unit/test_consensus_integrity.py),
and [`test_publish_e2e.py`](../ai-review/tests/integration/test_publish_e2e.py).

## Concurrency

GitLab serializes post per project/MR through a resource group. GitHub groups
workflow runs by PR and does not cancel an in-progress run; the stale-head guard
prevents an older run from mutating the newer revision. Custom invocation of the
Python post module has no distributed lock and must supply equivalent
serialization.

## Observability and artifacts

Start with `out/status/`, then the consensus and post artifacts. Record run
ID, source SHA, image digests, effective-config digest, panel status, failed and
resolution-eligible reviewers, post status, and `post_result.warnings`. GitLab
canonical prepare/review/critique artifacts expire after seven days;
consensus/post evidence expires after 30 days. GitHub follows repository/organization retention.
Export sanitized evidence before expiry.

Never retain credentials, CLI session files, sensitive prompts, raw proprietary
source beyond policy, or unnecessary model-authored content in evidence.

## Cost controls

Control cost by selecting models, disabling unused seats, setting reviewer
timeouts and finding caps, bounding diff/files/prompt size, and optionally
disabling critique (critique is a second model pass, so disabling it roughly
halves reviewer calls). Validate panel thresholds after changing seats. The
product does not currently provide the proposed per-reviewer token/cost
accounting, so provider billing remains the authoritative cost source;
record it per run when collecting live evidence.

For validation and lifecycle rehearsal without model spend, the deterministic
mock reviewer (`AI_REVIEW_LOCAL_MOCK=1` with `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
scenario via `AI_REVIEW_MOCK_SCENARIO`) drives the real posting/state path
with a canned finding set and no provider calls. Mock mode is forbidden in
production consumer projects: GitLab project/pipeline variables can override the
template's `AI_REVIEW_LOCAL_MOCK: "0"`, so the companion allow flag is required.
Every adapter fallback—including missing CLI or credential—requires the allow
flag. It is defense against accidental fallback, not an authorization boundary:
an actor able to inject both mock variables can enable mock mode. Production
templates therefore keep `AI_REVIEW_REQUIRE_REAL_*=1` as the fail-closed guard.
These are adapter controls that affect only review/critique behavior and are not
part of the effective-config digest, so set them consistently on the review and
critique jobs. Config-affecting overrides (critique/reviewer/panel) do feed the
digest, so scope any of those consistently across all jobs or consensus fails
closed on divergence.

## Image pin rotation

Build both images from one reviewed commit, capture immutable digests and
attestations, verify pulls, then update the canonical templates. Run
`make supply-chain` and `make docs-check`. Never combine a base image, reviewer
image, and trusted source SHA from different publication runs.

## Rollback and cleanup

Restore the previous complete workflow/template SHA and its matching images.
Start again at prepare and do not reuse artifacts from the failed version. A
credential rotation may change the platform bot identity; on GitLab that makes
old bot state untrusted and can cause a one-time repost. Preserve previous state
until the rollback run is verified.

Uninstall instructions are in the platform getting-started guides.

## Incident response

1. Stop automatic/manual review triggers, or disable the workflow or include,
   under the repository's incident policy.
2. Revoke suspected provider and platform credentials.
3. Preserve sanitized job IDs, source/image digests, artifacts, and logs without
   copying secret values or sensitive model content.
4. Determine whether exposure occurred in trusted jobs, reviewer subprocesses,
   platform comments/state, artifacts, or network egress.
5. Rotate credentials and bot identity deliberately; document state-ownership
   consequences.
6. Patch, rebuild from a reviewed commit, rerun hostile and functional evidence,
   and only then restore review triggers.
7. Report product vulnerabilities through [SECURITY.md](../SECURITY.md).
