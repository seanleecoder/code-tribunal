# Operations

## Upgrade from 0.4.x to 1.0

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
   `state.fail_closed_on_load_error` choice.
4. Configure one protected `GITLAB_TOKEN`. Verify panel blocking, resolution,
   and quorum thresholds against the enabled reviewer count.
5. Update the complete canonical workflow or the protected GitLab template SHA;
   keep base image, reviewer image, and trusted source SHA together.
6. Start a fresh run at prepare. Old finding/critique batches lack required
   quality/digest fields and must not be reused. Expanded effective-config
   hashing also invalidates old prepare manifests.
7. Expect a one-time update of existing bot-authored bodies when the render-body
   version changes. This should update existing identities, not duplicate them.
8. Verify state ownership, posting, commands, and the gate before enforcing it.

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
There is no supported installable Python distribution in 1.0.

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
| Post is `failed`, `partial_failed`, or `state_overflow` | Gate exits 7 even in advisory mode | Repair API/state capacity and rerun post/gate |
| Head changes before posting | No stale mutation; gate records stale/no-op success | Let the newer revision's run own the review |
| Blocking consensus with gate enabled | Gate exits 7 | Fix, dismiss with authorization, or change reviewed policy |
| Blocking consensus in advisory mode | Finding is reported but finding-based gate passes | Promote to enforcing only after validation |
| External fork lacks protected secrets | Canonical GitHub flow skips; GitLab topology withholds/fails safely | Use maintainer-controlled trusted review, never expose secrets to fork code |

“Fail closed” is therefore failure-class specific. Reviewer-seat loss can
degrade open; artifact integrity and post/state loss fail closed; stale-head
handling intentionally performs no mutation and yields to the newer run.
The precedence and exit behavior are exercised by
[`test_gate.py`](../ai-review/tests/unit/test_gate.py),
[`test_consensus_integrity.py`](../ai-review/tests/unit/test_consensus_integrity.py),
and [`test_post_gate_e2e.py`](../ai-review/tests/integration/test_post_gate_e2e.py).

## Concurrency

GitLab serializes post per project/MR through a resource group. GitHub groups
workflow runs by PR and does not cancel an in-progress run; the stale-head guard
prevents an older run from mutating the newer revision. Custom invocation of the
Python post module has no distributed lock and must supply equivalent
serialization.

## Observability and artifacts

Start with `out/status/`, then consensus, post, and gate artifacts. Record run
ID, source SHA, image digests, effective-config digest, panel status, failed and
resolution-eligible reviewers, post status, and gate reason. GitLab canonical
prepare/review/critique artifacts expire after seven days; consensus/post/gate
evidence expires after 30 days. GitHub follows repository/organization retention.
Export sanitized evidence before expiry.

Never retain credentials, CLI session files, sensitive prompts, raw proprietary
source beyond policy, or unnecessary model-authored content in evidence.

## Cost controls

Control cost by selecting models, disabling unused seats, setting reviewer
timeouts and finding caps, bounding diff/files/prompt size, and optionally
disabling critique (critique is a second model pass, so disabling it roughly
halves reviewer calls). Validate panel thresholds after changing seats. The
product does not currently provide the proposed per-reviewer token/cost
accounting from SPEC-20, so provider billing remains the authoritative cost
source; record it per run when collecting live evidence.

For validation and lifecycle rehearsal without model spend, the deterministic
mock reviewer (`AI_REVIEW_LOCAL_MOCK=1`, scenario via `AI_REVIEW_MOCK_SCENARIO`)
drives the real posting/state/gate path with a canned finding set and no provider
calls. Scope those variables pipeline-wide so prepare, review, critique, and
consensus agree on the effective-config digest.

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

1. Stop automatic/manual review triggers or remove the required check only under
   the repository's incident policy.
2. Revoke suspected provider and platform credentials.
3. Preserve sanitized job IDs, source/image digests, artifacts, and logs without
   copying secret values or sensitive model content.
4. Determine whether exposure occurred in trusted jobs, reviewer subprocesses,
   platform comments/state, artifacts, or network egress.
5. Rotate credentials and bot identity deliberately; document state-ownership
   consequences.
6. Patch, rebuild from a reviewed commit, rerun hostile and functional evidence,
   and only then restore enforcement.
7. Report product vulnerabilities through [SECURITY.md](../SECURITY.md).
