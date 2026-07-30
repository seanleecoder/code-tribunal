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
| GitLab | `seanleecoder/code-tribunal-ci-template` (project id `84667707`) | protected template project holding `ai-review/ci/` |

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

Public, project id `84667714`. Verified present:

- A runner.
- `OPENROUTER_API_KEY` and `GITLAB_TOKEN` (`api` scope), both **protected and
  masked**. The four behavioral toggles — `AI_REVIEW_CRITIQUE_ENABLED=true`,
  `AI_REVIEW_MERGE_GATE_ENABLED=true`, `AI_REVIEW_OPENCODE_ENABLED=true`,
  `AI_REVIEW_CURSOR_ENABLED=false` — are deliberately *unprotected*, so they apply on
  any ref including the hostile probe.
- `only_allow_merge_if_pipeline_succeeds = true` — this is what withholds the merge
  (`detailed_merge_status: ci_must_pass`). `merge_method = merge`.
- **Mock variables absent.** `AI_REVIEW_LOCAL_MOCK`, `AI_REVIEW_ALLOW_LOCAL_MOCK`,
  `AI_REVIEW_REQUIRE_REAL_*`, and `AI_REVIEW_MOCK_SCENARIO` are all gone, which is
  the correct resting state and means 1.0.0's demo-hygiene item was completed here.
  Re-verify before every Chain A run regardless — these are project-wide and sticky,
  and a stale `AI_REVIEW_LOCAL_MOCK=1` silently invalidates a real run.
- A `.gitlab-ci.yml` in the hardened child topology: a single `ai_review` trigger job
  with `inherit.variables: false`, `strategy: mirror`, both
  `forward.yaml_variables` and `forward.pipeline_variables` false, and exactly two
  same-project includes — `review-child.gitlab-ci.yml` and `review.gitlab-ci.yml` —
  at **one identical SHA**, currently `97e05fddf9f5466ccee385344a7aaeac500e4aa2`.

The **three GitLab pin variables** live in the template project's
`ai-review/ci/review.gitlab-ci.yml` `variables:` block, not in the consumer, and must
be replaced together: `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and
`AI_REVIEW_TRUSTED_IMAGE_SHA`. All three still carry the 1.0.0 pair. Push the update
as a new template commit and then point **both** consumer includes at that new SHA.

**Two branch classes, and the distinction is load-bearing:**

- **Protected** scratch source branch for every lifecycle MR (Chain A and Chain B).
  The protected `GITLAB_TOKEN` injects only on protected refs; from an unprotected
  branch it is withheld and prepare and posting fail outright. Protected today:
  `main`, `evidence/chain-a-88bc941`, `evidence/chain-b-88bc941`,
  `evidence/gitlab-lifecycle`, `evidence/gitlab-symlink-containment`, and the five
  `evidence/p0-symlink-{relative,parent,dangling,directory,proc-environ}` branches.
  **Protect each new `evidence/chain-*` branch before opening its MR** — this is the
  single most common cause of a lifecycle chain failing for the wrong reason.
- **Unprotected** source branch (or a fork) for the hostile-MR probe, where the
  withholding *is* the result being measured. Unprotected today:
  `hostile/unprotected-88bc941`, `evidence/p0-hostile-forwarding`, and
  `evidence/gitlab-hostile-boundary`.

The `evidence/p0-symlink-*` branches already carry `120000` symlink tree entries —
reuse them rather than trying to create one through the commits API, which cannot make
a symlink entry. That is the recorded way to close the live symlink-variant gap
without SSH push access.

MR history: 1.0.0 used `!10` (Chain A, protected), `!11` (Chain B, protected), and
`!12` (hostile, unprotected `hostile/unprotected-88bc941`). `!1`–`!9` are the earlier
P0 symlink and hostile-forwarding fixtures. All are closed; none should be deleted.

Mock toggles go in as **project** CI/CD variables, not manual "Run pipeline"
variables: project variables apply to push-triggered `merge_request_event` pipelines
and reach the child where forwarding is disabled, whereas manual variables are
dropped by any push-triggered pipeline and would silently run real. Flip
`AI_REVIEW_MOCK_SCENARIO` by editing the project variable in place and re-triggering
with `POST /projects/84667714/merge_requests/:iid/pipelines` — never pipeline
*retry*, which only re-runs failed jobs and so re-runs the gate instead of
re-driving prepare→post. Project variables are sticky and project-wide: delete them
before any Chain A run and after every Chain B campaign.

Preserve GitLab note `3601861614` on MR `!11` (created `2026-07-25 20:47:29`, updated
`20:59:24`) for the same body-refresh reason as the GitHub comment above.

## Per-release setup checklist

1. Confirm the mock variables are absent on **both** consumers. Both are currently
   clean; verify anyway, because a leftover toggle turns a real run into a fake one.
2. Copy the workflow / CI template from the new `R`; repin the six GitHub container
   digests and the three GitLab pin variables (`AI_REVIEW_BASE_IMAGE`,
   `AI_REVIEW_REVIEWER_IMAGE`, `AI_REVIEW_TRUSTED_IMAGE_SHA`) to the new pair.
3. Land the GitHub adoption change as a PR — the required check blocks direct pushes
   to `main` — and run that PR in mock mode so it costs nothing.
4. Push the repinned template as a new commit to `code-tribunal-ci-template`, then
   point **both** consumer includes at that new SHA. Two different SHAs, or a stale
   template pin, means the evidence exercised the wrong images.
5. Confirm the GitHub ruleset still lists `gate` as required and GitLab
   `only_allow_merge_if_pipeline_succeeds` is still true. Enforcement being off
   silently turns the blocking step into a self-report.
6. Protect the new `evidence/chain-*` GitLab branches before opening their MRs.
7. Run Chain A first, then Chain B; delete the mock variables afterwards and confirm
   they are gone.
