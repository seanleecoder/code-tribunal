# Evidence record: GitLab current-image lifecycle / 2026-07-25

Status: passed

Release-runtime-source: 88bc9412b283d4a44328ab3ffd9f9708b0290f8e
Release-base-digest: sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
Release-reviewer-digest: sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe

Covers evidence-matrix row **GitLab current image**: inline create/update, human
commands, state persistence, and the real merge block. Procedure:
[evidence README, "Current-image lifecycle procedure"](README.md); runbook:
[Chain B](RUNBOOK-1.0-rc.md).

## Identity

- Platform: GitLab.com SaaS, shared runners
- Date/time: 2026-07-25, ~20:44–21:07 UTC
- Deployment topology: hardened mirrored child — exactly two same-project,
  same-SHA includes with `inherit.variables: false` and both
  `trigger.forward` flags disabled
- Consumer project: `seanleecoder/code-tribunal-demo` (project id `84667714`),
  default-branch config at `fba6e1ebc96f43a50ea71825759a8d0c5b456ca4`
- Template project: `seanleecoder/code-tribunal-ci-template@97e05fddf9f5466ccee385344a7aaeac500e4aa2`
- Change request: MR !11, from **protected** source branch
  `evidence/chain-b-88bc941`
- Source commit under review: `67d85137cd74324cf303da5b1612b88c864e6c45`
  (a **modification** of `src/access.py` adding a `records[0]` indexing marker, so
  the mock anchor is stable and resolvable — see the runbook note on added files)
- Runtime source: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- Publication run: `30125524008`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`

## Preconditions

- `OPENROUTER_API_KEY` and `GITLAB_TOKEN` (`api` scope) configured as
  **protected + masked** project variables; no secret value recorded here. The
  source branch is protected so `GITLAB_TOKEN` injects and posting can occur.
- **Pipelines must succeed** enabled (`only_allow_merge_if_pipeline_succeeds: true`).
- Deterministic mock enabled for the whole chain: `AI_REVIEW_LOCAL_MOCK=1`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, all four `AI_REVIEW_REQUIRE_REAL_*=0`, and
  `AI_REVIEW_MOCK_SCENARIO` set per step — supplied as **project** CI/CD variables
  so they reach the hardened child. **Zero model tokens spent.**
- Critique enabled; Cursor disabled; merge gate enabled.
- Expected behavior: one finding identity created once, untouched on an unchanged
  rerun, updated in place on a body-only change, resolved and kept resolved by a
  human command, reopened by a human command, and blocking the merge throughout.

## Actual result

All steps ran on one finding identity — issue id
`4f51a7af75ec457a69f687be2e15363c9c59b047290f8efdcda8784aa2fa9ff9`, GitLab
discussion `903db1c64759208baeaa1776a1d8114392dbb8f4`, root note `3601861614`,
anchored to `src/access.py` line 15. Note the issue id is **identical** to the
GitHub lifecycle record's: finding identity is platform-independent, derived from
reviewer, path, category, side, `context_hash`, and title fingerprint.

| Step | Scenario | Parent / child pipeline | `post_result` | Platform observation |
|---|---|---|---|---|
| 1. create | `blocking` | `2705746042` / `2705746053` | `created: 1` | discussion created, root note `3601861614` at 20:47:29 |
| 2. unchanged rerun | `blocking` | `2705748310` / `2705748321` | `created: 0, updated: 0, skipped_unchanged: 1` | no duplicate discussion |
| 3. changed body | `blocking_alt` | `2705753334` / `2705753349` | `created: 0, **updated: 1**` | same note `3601861614`, `updated_at` 20:59:24, body now the alternate text, still unresolved |
| 4. resolve | `blocking` | `2705756061` / `2705756078` | `resolved: 1, skipped_unchanged: 1, warnings: []` | root note `resolved: true` |
| 5. reopen | `blocking` | `2705757736` / `2705757749` | `skipped_unchanged: 1` | root note `resolved: false`, same note id, identity preserved |

Run identifiers, in order: `gl-2705746053-15534925484`,
`gl-2705748321-15534938056`, `gl-2705753349-15534963611`,
`gl-2705756078-15534977601`, `gl-2705757749-15534986644`.

- Consensus on every step: `panel_status: full`, `panel_convergence: 1.0`,
  `surface_count: 1`, `drop_count: 0`, `fyi_count: 0`, `block_merge: true`.
- `ai_review_gate` failed (`script_failure`) on every step, as intended for a
  blocking consensus, and MR !11 reported
  `detailed_merge_status: ci_must_pass` throughout — a genuine platform merge
  block under "Pipelines must succeed", not an advisory signal.
- **Step 3 is the positive changed-body in-place update**, the path the evidence
  index recorded as never demonstrated live on either platform. `blocking_alt`
  keeps identity and changes only the body; `post_result` reported
  `updated_discussions: 1` with `action: "updated"` on the same
  `discussion_id`/`issue_id`, and the platform confirmed the existing note was
  rewritten (`created 20:47:29` → `updated 20:59:24`) rather than duplicated.
- **Step 4 human command authorization:** `/ai-review wontfix` was posted as a
  note on the discussion by a Developer-or-higher author and accepted —
  `post_result.warnings` was empty, so it was not rejected for access. As on
  GitHub, the dismissal did not clear the gate: consensus still reported
  `block_merge: true`, matching the documented gate precedence and the documented
  meaning of `wontfix` as a durable dismissal rather than a gate override.

## Audit

- Artifacts inspected per step: `out/consensus/consensus.json` and
  `out/post/post_result.json` downloaded from the child pipelines' consensus and
  post jobs.
- Logs inspected: child job statuses for every pipeline above; the hostile-boundary
  traces are audited in the [hostile-MR record](record-gitlab-hostile-mr.md).
- Platform objects verified directly through the GitLab API: discussion
  `903db1c6…` root note id, `created_at`, `updated_at`, `resolved`, and
  `position` (`src/access.py` line 15), plus MR `detailed_merge_status` at each
  step.
- Credential values absent: no credential value is reproduced in this record, and
  the credential-withholding audit is covered by the hostile-MR record.
- Sensitive model content omitted: this chain is deterministic mock output, so no
  real model content is involved.
- Known unexercised paths:
  - Step 6 (unrelated line movement) deliberately not run live; the internal remap
    is regression-covered and only platform-visible re-anchoring is live-optional.
  - The stale-head no-op is a GitHub-specific step and was exercised there
    ([GitHub lifecycle record](record-github-current-image.md)), not here.
  - The below-quorum FYI/summary-comment path and the inline-unmappable summary
    fallback are unreachable from the mock and remain regression-covered.
  - This chain used the deterministic mock, so it proves posting/state/gate
    behavior only. A real-model panel was additionally run on this platform
    (MR !10, child pipeline `2705723423`) and is recorded as supporting evidence in
    the [default-model smoke record](record-github-default-model-smoke.md); that
    panel was `degraded`, not `full`, because the OpenCode seat omitted a required
    `confidence` field.

## Verdict

Scoped pass for GitLab.com on `seanleecoder/code-tribunal-demo` at runtime source
`88bc9412b283d4a44328ab3ffd9f9708b0290f8e` with the release-pinned image pair, in
the hardened mirrored-child topology: a single finding identity was created once,
skipped unchanged, **updated in place on a body-only change**, resolved by an
authorized human command, and reopened — while "Pipelines must succeed" genuinely
withheld the merge throughout. This closes the changed-body in-place update gap
for GitLab. It proves posting, state, command authorization, and gate behavior for
this topology only, and makes no claim about real-model behavior or about the
hostile-MR boundary, which are recorded separately.

## Superseded candidates

Historical provenance only, not a release binding: partial lifecycle evidence at
runtime sources `15d424feea730a04338ed423bf93b8797d807bbc` and
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` (the latter invalidated by the GitHub
human-command authorization defect), which proved a real consumer flow but never
the positive changed-body in-place update.
