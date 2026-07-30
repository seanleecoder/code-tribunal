# Evidence consumer projects

The live-evidence runs in [`RUNBOOK.md`](RUNBOOK.md) need real consumer projects with
real runners, protected credentials, and real merge enforcement. Two long-lived,
operator-controlled, **public** scratch projects serve that purpose. They were used
for the whole 1.0.0 campaign and are the projects to reuse for every subsequent
release — re-creating them from scratch each release wastes hours and loses the
pre-v3 threads that the body-refresh check depends on.

| Platform | Project | Role |
|---|---|---|
| GitHub | <https://github.com/seanleecoder/code-tribunal-demo> | consumer: workflow copy, secrets, required-check ruleset |
| GitLab | <https://gitlab.com/seanleecoder/code-tribunal-demo> (project id `84667714`) | consumer: `.gitlab-ci.yml`, protected/masked variables, runner |
| GitLab | `seanleecoder/code-tribunal-ci-template` | protected template project holding `ai-review/ci/` |

Both consumers are deliberately public and hold no proprietary content, which is why
naming them here does not violate the sanitization rule in
[`README.md`](README.md) — that rule exists to keep *private* consumer coordinates
out of this repository. Never record credential values, session material, or model
content from these projects regardless.

## GitHub consumer — `seanleecoder/code-tribunal-demo`

Public, default branch `main`. Contains a small `src/` + `tests/` Python fixture tree
and a single workflow `.github/workflows/ai-review.yml` copied from the product
repo's canonical template.

**Already configured — do not rebuild this, only repin it:**

- **Secrets:** `OPENROUTER_API_KEY`, `AI_REVIEW_GITHUB_RESOLVE_TOKEN`.
- **Ruleset** "Require AI Review gate" (id `19420757`), active, targeting
  `~DEFAULT_BRANCH`, with `gate` as a **required status check**
  (`strict_required_status_checks_policy: false`). This is what makes the blocking
  step a genuine merge block rather than a self-reported one. Because a required
  check also blocks direct pushes to `main`, adopting a workflow change has to go
  through a PR — run that PR in mock mode so it costs nothing.
- **Repository variables** (persisted): `AI_REVIEW_CRITIQUE_ENABLED=true`,
  `AI_REVIEW_MERGE_GATE_ENABLED=true`, `AI_REVIEW_OPENCODE_ENABLED=true`,
  `AI_REVIEW_CURSOR_ENABLED=false`, `AI_REVIEW_MANUAL=false`.
- **The mock-variable mapping is already in the workflow.** The one-time edit the
  runbook describes has been made: the review and critique steps read
  `AI_REVIEW_LOCAL_MOCK: ${{ vars.AI_REVIEW_LOCAL_MOCK || '0' }}`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK: … || 'false'`,
  `AI_REVIEW_MOCK_SCENARIO: ${{ vars.AI_REVIEW_MOCK_SCENARIO }}`, and each
  `AI_REVIEW_REQUIRE_REAL_*: … || '1'`. So Chain A runs safely with the variables
  unset, and Chain B is driven purely by setting repository variables — no workflow
  commit, which matters because a new commit on the reviewed branch changes the diff
  and moves the mock's selected anchor.
- **Mock variables are currently absent**, which is the intended resting state: with
  them gone the `|| '1'` defaults restore `AI_REVIEW_REQUIRE_REAL_*=1`, so a run that
  somehow still reached the mock adapter fails closed instead of quietly producing
  fake "real" evidence. **Delete them again after every Chain B campaign.**

**What changes per release:** the six container digest pins in the workflow (jobs
`prepare`, `review`, `critique`, `consensus`, `post`, `gate`). As of this writing they
are still the 1.0.0 pair, `1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e` with base
`sha256:f2a433ac…` and reviewer `sha256:2fd84c43…`. Copy the workflow from the new `R`
rather than hand-editing pins: an older copy can carry env keys that a newer `R`
rejects (the `AI_REVIEW_PANEL_GROUPING_SEMANTIC_*` overrides were one such case).

### Branch and PR conventions

Evidence branches use an `evidence/` prefix, and the adoption PR a `chore/` prefix.
Existing branches, all worth keeping:

| Branch | Purpose |
|---|---|
| `evidence/chain-a-<R-short>` | real default-model panel (Chain A). One per release. |
| `evidence/chain-b-<R-short>` | deterministic mock lifecycle (Chain B). One per release. |
| `evidence/github-lifecycle` | original finding-lifecycle fixture |
| `evidence/github-revision-race`, `evidence/github-revision-staging`, `evidence/github-manifest-base`, `evidence/github-manifest-head` | SPEC-34 revision-boundary fixtures |
| `evidence/p0-template` | template-adoption fixture |
| `chore/adopt-canonical-workflow-<R-short>` | the repin PR; merged, not squashed |

1.0.0 used PR #4 to adopt the workflow at `R = 88bc941`, PRs #5/#6 for Chain B, and
PR #7 for Chain A. Chain A and Chain B must use **separate** PRs and separate finding
identities: the real panel emits a model-authored finding whose identity you do not
control, so continuing it with the mock opens a new discussion instead of updating
one.

### Threads that must not be deleted

The `render-body.v3` refresh check needs a bot-authored thread created by an
**older** image. Preserve GitHub comment `3650942127` on the 1.0.0 Chain B PR
(created `2026-07-25 20:13:42`, updated `20:20:37`, `issue_id` shared with the GitLab
note below). Closing the PR is fine; deleting the comment or the branch is not.

## GitLab consumer — `seanleecoder/code-tribunal-demo`

Public, project id `84667714`. Requires, and already has:

- A runner.
- Protected **and** masked `OPENROUTER_API_KEY` and `GITLAB_TOKEN` (`api` scope).
- **Pipelines must succeed** enabled, which is what withholds the merge
  (`detailed_merge_status: ci_must_pass`).
- A `.gitlab-ci.yml` referencing the protected template project
  `seanleecoder/code-tribunal-ci-template` at one pinned SHA — **the same SHA for
  both includes**. 1.0.0 used `97e05fddf9f5466ccee385344a7aaeac500e4aa2`. The
  hardened child topology requires exactly two same-project, same-SHA includes with
  `inherit.variables: false` and both forwarding flags disabled.

**Two branch classes, and the distinction is load-bearing:**

- **Protected** scratch source branch for every lifecycle MR (Chain A and Chain B).
  The protected `GITLAB_TOKEN` injects only on protected refs; from an unprotected
  branch it is withheld and prepare and posting fail outright.
- **Unprotected** source branch (or a fork) for the hostile-MR probe, where the
  withholding *is* the result being measured. 1.0.0 used MR `!11` (protected,
  lifecycle) and MR `!12` (unprotected, hostile), plus the pre-existing
  `evidence/p0-symlink-*` branches which already carry `120000` symlink tree entries
  — reuse those rather than trying to create one through the commits API, which
  cannot make a symlink entry.

Mock toggles go in as **project** CI/CD variables, not manual "Run pipeline"
variables: project variables apply to push-triggered `merge_request_event` pipelines
and reach the child where forwarding is disabled, whereas manual variables are
dropped by any push-triggered pipeline and would silently run real. Flip
`AI_REVIEW_MOCK_SCENARIO` by editing the project variable in place and re-triggering
with `POST /projects/84667714/merge_requests/:iid/pipelines` — never pipeline
*retry*, which only re-runs failed jobs and so re-runs the gate instead of
re-driving prepare→post. Project variables are sticky and project-wide: delete them
before any Chain A run and after every Chain B campaign.

Preserve GitLab note `3601861614` (created `2026-07-25 20:47:29`, updated
`20:59:24`) for the same body-refresh reason as the GitHub comment above.

**1.0.0 left one hygiene item unmet here:** stale mock CI/CD variables were not
confirmed deleted from this project. Verify and delete them before the next Chain A
run — a stale `AI_REVIEW_LOCAL_MOCK=1` silently invalidates a real run.

## Per-release setup checklist

1. Confirm the mock variables are absent on **both** consumers.
2. Copy the workflow / CI template from the new `R`; repin the six GitHub digests and
   the three GitLab pin variables to the new pair.
3. Land the adoption change as a PR/MR (required checks block direct pushes); run
   that PR in mock mode.
4. Push the new template SHA to `code-tribunal-ci-template` and update **both**
   includes in the GitLab consumer to it.
5. Confirm the GitHub ruleset still lists `gate` as required, and that GitLab
   **Pipelines must succeed** is still on. Enforcement being off silently turns the
   blocking step into a self-report.
6. Run Chain A first, then Chain B; delete the mock variables afterwards.
