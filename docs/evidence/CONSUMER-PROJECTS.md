# Evidence consumer projects

The Candidate Canary and the manual checks in [`RUNBOOK.md`](RUNBOOK.md) need real
consumer projects with real runners and protected credentials. Two long-lived,
operator-controlled, **public** scratch projects serve that purpose. They were used
for the whole 1.0.0 campaign and are the projects to reuse for every subsequent
release — re-creating them from scratch each release wastes hours and loses the
older-format threads that the body-refresh check depends on.

Deployment observations below are dated evidence, the latest being the
[3.1.0 adoption](#resting-deployment-adoption-310-2026-11-15) on 2026-11-15, not
a fresh inspection of either service. Before each campaign, run
`make demo-preflight` (step 0 of the
[per-release setup checklist](#per-release-setup-checklist)) for credentials and variables, and recheck by
hand what it does not inspect: branch protection, project settings and pins.

| Platform | Project | Role |
|---|---|---|
| GitHub | <https://github.com/seanleecoder/code-tribunal-demo> | consumer: workflow caller, secrets, repository variables |
| GitLab | <https://gitlab.com/seanleecoder/code-tribunal-demo> (project id `84667714`) | consumer: `.gitlab-ci.yml`, protected/masked variables, runner |
| GitLab | `seanleecoder/code-tribunal-ci-template` (project id `84667707`) | protected template project holding `ai-review/ci/` |

Both consumers are deliberately public and hold no proprietary content, which is why
naming them here does not violate the sanitization rule in
[`README.md`](README.md) — that rule exists to keep *private* consumer coordinates
out of this repository. Never record credential values, session material, or model
content from these projects regardless.

## GitHub consumer — `seanleecoder/code-tribunal-demo`

Public, default branch `main`. Contains a small `src/` + `tests/` Python fixture tree.
`.github/workflows/ai-review.yml` is the release-SHA caller from the
[GitHub installation guide](../getting-started/github.md), with inherited
secrets. The canary's temporary branches install their own caller and
candidate-pinned reusable workflow, independently of this resting deployment.

**Recorded configuration:**

- **Secrets:** `OPENROUTER_API_KEY`, `CURSOR_API_KEY`,
  `AI_REVIEW_GITHUB_RESOLVE_TOKEN`.
- **No branch ruleset and no required status check.** The "Require AI Review gate"
  ruleset (id `19420757`) was deleted on 2026-09-24, and the separate classic branch
  protection on `main`, which also required `gate`, had its required status checks
  removed on 2026-09-27; Code Tribunal informs and never gates merges. Classic
  protection otherwise remains (no force pushes, no deletions). `main` accepts direct pushes, but land a
  workflow adoption as a PR anyway so the caller runs once before it is merged.
- **Repository variables** (persisted): `AI_REVIEW_MANUAL=false` (the GitHub Chain B procedure re-runs automatic
  `pull_request` runs, which manual mode skips),
  `AI_REVIEW_REVIEWERS=claude, codex, opencode, cursor`, and model and effort
  variables for every seat, set to the shipped defaults as of 3.1.0. When a
  release changes a default, update them before the canary; canaries clear
  effort variables but retain model variables.
  The roster variable must include `cursor`: the canonical workflow reads it before exposing
  `CURSOR_API_KEY`. Keep this four-seat resting roster for `make demo-preflight`;
  scope an exact three-seat default smoke to its temporary workflow, then rerun
  preflight after cleanup. Retired variables such as the per-seat
  `AI_REVIEW_*_ENABLED` flags and `AI_REVIEW_MERGE_GATE_ENABLED` are absent and
  must stay so: a current image fails at config load on any unknown `AI_REVIEW_`
  variable.
- **Mock variables were recorded absent**, which is the intended resting state.
  **Delete them again after every Chain B campaign.** The production caller
  exposes no mock switch; deterministic checks use the canary's temporary
  caller/reusable pair.

**What changes per release:** the caller's release SHA. Both image pins
live in the called workflow. Do not paste the reusable implementation into a
caller file; it has no automatic trigger. Temporary mock checks use the canary's
caller/reusable pair instead of enabling mocks through the production caller.

**Resting deployment:** as of 2026-11-15 `main` calls the reusable workflow at
`ef8a13b49c08e625b026f391c646f51a0d012888` (`v3.1.0`), adopted by PR #59 (merge
`f557518`). The previous deployment was the copied 2.0.0 workflow at `b44ab38`.
This is recorded evidence, not a current pin recommendation.

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
PR #7 for Chain A. 1.0.2 used PR #14 for Chain A. PRs #15/#16 validated SPEC-39 at
`09f4e65`, PR #17 adopted `451472d`, PR #18 is its Chain A, and PRs #19–#21 were
opened and closed by the Candidate Canary. 2.0.0 used canary PR #23, adoption PR
#24, Chain B PR #25, and reopened PR #6 for the `render-body.v4` refresh. Chain A and Chain B must use **separate** PRs and separate finding
identities: the real panel emits a model-authored finding whose identity you do not
control, so continuing it with the mock opens a new discussion instead of updating
one.

PRs #9, #10, #14, #18 and #25 were closed on 2026-11-15, before the 3.1.0
adoption. Each keeps its branch, comments and **v1** state ledger. A 3.x image
refuses that ledger, so convert it with `scripts/migrate_state_v2.py` from the
`v3.1.0` tag before reopening one.

### Threads that must not be deleted

The posted-body refresh check needs a bot-authored thread created by an **older**
image. Preserve GitHub comment `3650942127` on the 1.0.0 Chain B PR #6
(created `2026-07-25 20:13:42`, `issue_id` shared with the GitLab note below). The
1.0.1 image refreshed it to `render-body.v3` and the 2.0.0 candidate to
`render-body.v4`. The 3.0 candidate at `e95335b` refreshed the same root in
[run 41345703863](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41345703863)
and skipped it as unchanged in
[run 41345948621](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41345948621).
The author, creation time and finding identity were preserved. State v2 now lives
in summary comment `6276182501`; retain the original v1 exports for rollback.
Run the next refresh with the
[body-format refresh](RUNBOOK.md#body-format-refresh) procedure on
`evidence/chain-b-88bc941`, after confirming its diff is still only the fixture.
Closing the PR is fine; deleting the comment or the branch is not.

## GitLab consumer — `seanleecoder/code-tribunal-demo`

Public, project id `84667714`. Recorded configuration:

- A runner.
- `OPENROUTER_API_KEY`, `CURSOR_API_KEY` and `GITLAB_TOKEN` (`api` scope), all
  **protected and masked**. Retired variables such as
  `AI_REVIEW_CRITIQUE_ENABLED`, the per-seat `AI_REVIEW_*_ENABLED` flags and
  `AI_REVIEW_MERGE_GATE_ENABLED` are absent and must stay so: a current image
  fails at config load on any unknown `AI_REVIEW_` variable.
- Protected, unmasked roster, model, and effort variables with the same values
  as the GitHub consumer's repository variables.
- **Pipeline variables are restricted** (minimum role "no one allowed", set on
  2026-11-15), the second control in
  [protect the CI composition](../getting-started/gitlab.md#protect-the-ci-composition).
  `web` and `api` pipelines therefore cannot supply `CI_MERGE_REQUEST_IID` or
  override the image pins. The CI file deliberately stays in the demo project,
  with no external CI configuration, because the hostile campaign must supply
  its own `.gitlab-ci.yml` on an unprotected branch.
- `only_allow_merge_if_pipeline_succeeds = true` — this is what withholds the merge
  (`detailed_merge_status: ci_must_pass`). `merge_method = merge`.
- **Mock variables absent.** `AI_REVIEW_LOCAL_MOCK`, `AI_REVIEW_ALLOW_LOCAL_MOCK`,
  and `AI_REVIEW_MOCK_SCENARIO` are all gone, which is the correct resting state.
  Re-verify before every Chain A run regardless — these are project-wide and sticky,
  and a stale `AI_REVIEW_LOCAL_MOCK=1` silently invalidates a real run.
- `.gitlab-ci.yml` on `main` is the documented
  [direct include](../getting-started/examples/gitlab-direct.yml) of the template
  at `370da58647aaa88332c4345fbc6d6f31b8859d5e` (3.1.0), adopted by MR `!65`
  (merge `0197be8`). The previous deployment was the 2.0.0 child trigger at
  template `f8ae213`, consumer commit `5900e34`. The canary's temporary branches
  install a direct include at the candidate template SHA, independently of this
  resting deployment.

Before a direct-include panel or lifecycle campaign, confirm the demo's **Allow
merge request pipelines to access protected variables and runners** setting is
enabled and `main` remains
protected. The orchestrator protects each panel/lifecycle source branch; the
hostile source stays unprotected. `make demo-preflight` checks credential
inventory and flags, but does not inspect this project setting.

The **three GitLab pin variables** live in the template project's
`ai-review/ci/review.gitlab-ci.yml` `variables:` block, not in the consumer, and must
be replaced together: `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and
`AI_REVIEW_TRUSTED_IMAGE_SHA`. Check their current values in the template project
before the repin; do not trust a value recorded here. Push the update
as a new template commit and then point the consumer's direct include at that new SHA.

**Two branch classes, and the distinction is load-bearing:**

- **Protected** scratch source branch for every lifecycle MR (Chain A and Chain B).
  The protected `GITLAB_TOKEN` injects only on protected refs; from an unprotected
  branch it is withheld and prepare fails before posting. Recorded protected branches:
  `main`, `evidence/chain-a-88bc941`, `evidence/chain-b-88bc941`,
  `evidence/gitlab-lifecycle`, `evidence/gitlab-symlink-containment`, and the five
  `evidence/p0-symlink-{relative,parent,dangling,directory,proc-environ}` branches.
  **Protect each new `evidence/chain-*` branch before opening its MR** — this is the
  single most common cause of a lifecycle chain failing for the wrong reason.
- **Unprotected** source branch (or a fork) for the hostile-MR probe, where the
  withholding *is* the result being measured. Recorded unprotected branches:
  `hostile/unprotected-88bc941`, `evidence/p0-hostile-forwarding`, and
  `evidence/gitlab-hostile-boundary`.

The `evidence/p0-symlink-*` branches already carry `120000` symlink tree entries —
reuse them rather than trying to create one through the commits API, which cannot make
a symlink entry. That is the recorded way to close the live symlink-variant gap
without SSH push access.

MR history: 1.0.0 used `!10` (Chain A, protected), `!11` (Chain B, protected), and
`!12` (hostile, unprotected `hostile/unprotected-88bc941`). 2.0.0 used canary `!21`,
`!22` (Chain B, protected `evidence/chain-b-71dfabc`), and `!23` (hostile,
unprotected `hostile/unprotected-71dfabc`). `!1`–`!9` are the earlier
P0 symlink and hostile-forwarding fixtures. `!15` (Chain A at `451472d`) was
closed on 2026-11-15 and keeps a **v1** state ledger; convert it as described for
the GitHub chains before reopening it. All are closed; none should be deleted.

Mock toggles go in as **project** CI/CD variables, not manual "Run pipeline"
variables: project variables apply to push-triggered `merge_request_event`
pipelines, whereas manual variables are dropped by push-triggered pipelines and
would silently run real. Flip
`AI_REVIEW_MOCK_SCENARIO` by editing the project variable in place and re-triggering
with `POST /projects/84667714/merge_requests/:iid/pipelines` — never pipeline
*retry*, which only re-runs failed jobs and so does not re-drive prepare→post. Project variables are sticky and project-wide: delete them
before any Chain A run and after every Chain B campaign.

Preserve GitLab note `3601861614` on MR `!11` (created `2026-07-25 20:47:29`, updated
`20:59:24`) for the same body-refresh reason as the GitHub comment above. The 3.0
candidate refreshed it in place; see the
[preserved GitLab refresh fixture](#preserved-gitlab-refresh-fixture).

## Per-release setup checklist

0. Run `make demo-preflight` (read-only; needs authenticated `gh` and `glab`) and
   fix every `FAIL`. The docstring in `scripts/demo_preflight.py` lists what it
   checks, including leftover mock variables on **both** consumers.
1. Dispatch the [Candidate Canary](../../CONTRIBUTING.md#candidate-canary)
   against the new pair. It needs no repin: it pushes its own temporary branch and
   closes its PR/MR afterwards. Do not repin until it is green.
2. Point the GitHub caller to the reviewed release commit `P`. Copy the GitLab template from `P`
   with its three pin variables (`AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`,
   `AI_REVIEW_TRUSTED_IMAGE_SHA`) set to the tested pair.
   Confirm `AI_REVIEW_MERGE_GATE_ENABLED` is still absent from both consumers; it
   is retired and fails the run.
3. Land the GitHub adoption change as a PR and verify it using the
   [GitHub installation guide](../getting-started/github.md#first-run-and-verification).
   Use the temporary canary pair for deterministic mock checks; the persistent
   production caller exposes no mock switch.
4. Push the repinned template as a new commit to `code-tribunal-ci-template`, then
   point the consumer's direct include at that new SHA. A stale template pin
   means the evidence exercised the wrong images.
5. Confirm no GitHub ruleset requires the retired `gate` check (a leftover entry
   makes every PR unmergeable) and that GitLab
   `only_allow_merge_if_pipeline_succeeds` is still true. On GitLab that setting
   is what makes a *publication* failure withhold the merge; it says nothing
   about findings.
6. Protect the new `evidence/chain-*` GitLab branches before opening their MRs.
7. After any manual mock run, follow the
   [mock cleanup](RUNBOOK.md#the-deterministic-mock-reviewer).

### Preserved GitLab refresh fixture

MR `!11` retains root note `3601861614`, authored by bot `40508593` on
2026-07-25. The 3.0 candidate at `e95335b` refreshed it in place in
[pipeline 3015320769](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3015320769),
with one update, no creates and no warnings. Its
[unchanged rerun](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3015327225)
made no thread edits. State v2 reuses note `3601861625`. The fixture is closed,
its previous CI configuration is restored, and the temporary direct-include
refresh template branch is deleted. Preserve the root, branch and v1 exports;
adopt the current trusted topology before reopening the fixture again.

### External reusable-workflow caller (3.0 candidate)

[Demo PR #49](https://github.com/seanleecoder/code-tribunal-demo/pull/49) calls
`seanleecoder/code-tribunal/.github/workflows/ai-review-reusable.yml` at
`8092ea76fe7950e669781e75323dfafc709b620b`, using named secrets. GitHub's run
metadata confirms that external workflow SHA. Both the
[automatic run](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41346849838)
and [manual run](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41347108804)
completed review and critique on all four seats with full run health and a
successful `ai-review / post`. The manual manifest and health rows captured a
caller-only Codex `medium` effort override; the resting value was restored to
`low`. Caller roster/model values matched the prepared configuration. The PR is
closed without merging and the persistent deployment is unchanged.

This evidence is for the `e95335b` image pair. The live fork-skip check is a
[carried coverage gap](RUNBOOK.md#carried-coverage-gaps). The full release canary
must also pass independently.

### Exact-pair 3.0 cutover acceptance

The final candidate runtime is `a9208beed5ef0244ebfad9602937c59469fcb944`;
[image publication 41351512443](https://github.com/seanleecoder/code-tribunal/actions/runs/41351512443)
built and attested both images. The release templates pin the base digest
`sha256:46a22aa49e9df4c1a09523df9960bfed0e59772bfef3bdfc7c62ea8d55637dd6`
and reviewer digest
`sha256:6c4259eb7a3a95b3f2568033856c5c1e50aaefb181b0bcf76b5eb660af911e83`.
The earlier candidate's failed full canary is not release assurance; the fresh
[full canary 41353175842](https://github.com/seanleecoder/code-tribunal/actions/runs/41353175842)
tests this pair with `panel,lifecycle,hostile`.

With the preserved fixtures closed, acceptance repeated conversion from the
retained original authenticated v1 exports, saved the current notes, applied the
converted v2 bodies to the existing summary IDs, and verified exact readback.
The earlier rehearsal had already removed the obsolete v1 note; no historical
thread or human reply was deleted. Fresh prepare-through-post runs then used
this exact image pair:

- GitHub [automatic run 41353409925](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41353409925)
  updated root `3650942127` once, created no threads and reported no warnings.
  [Manual run 41353526257](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/41353526257)
  skipped the unchanged thread with no edits or warnings. Both used an external
  SHA-pinned reusable workflow at `299dbc59793b44afabf0278a95acd4d0dd8391dc`,
  inherited secrets and temporary deterministic mock seats. The manual manifest
  and health report captured the caller's Codex `medium` override; its repository
  variable was restored to `low`. A queued event for the old fixture head failed
  safely in prepare before diff collection; it is not an acceptance run.
- GitLab [pipeline 3015434694](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3015434694)
  updated root `3601861614` once, created no threads and reported no warnings.
  [Unchanged pipeline 3015438995](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3015438995)
  skipped it without edits or warnings. Both used a protected direct include,
  the candidate image pair and temporary deterministic mock seats.

Both roots retained their original author, creation time and finding identity;
the v2 records retained the human `reopen` disposition and original discussion
IDs. GitHub summary `6276182501` and GitLab summary `3601861625` remain the state
containers. Both fixtures were closed again, their previous CI files restored,
and temporary workflow/template branches deleted. Original v1 exports remain
outside version control for the paused-review
[rollback procedure](https://github.com/seanleecoder/code-tribunal/blob/v3.1.1/docs/operations.md#state-v2-migration). Historical multiline
reader behavior also remains covered by the regression suite; these preserved
live roots have single-line positions.

The real panel on both platforms completed all four review and critique seats
with full health. Its prepared model and effort routes match the shipped
defaults, so it supplies default-route evidence without a separate three-seat
run. The temporary mock caller checks prove forwarding and state handling, not
provider behavior for non-default efforts or the pending live fork skip.

## Resting deployment adoption (3.1.0, 2026-11-15)

Both resting deployments moved from 2.0.0 to the released 3.1.0 pair. The six
open evidence change requests were closed first (GitHub #9, #10, #14, #18, #25
and GitLab `!15`), so no v1 ledger met a 3.x image; the preserved refresh
fixtures already hold state v2 from the
[3.0 acceptance](#exact-pair-30-cutover-acceptance).

- **Template project:** commit `370da58647aaa88332c4345fbc6d6f31b8859d5e` holds
  `ai-review/ci/review.gitlab-ci.yml` byte-identical to `v3.1.0` and removes the
  child wrapper.
- **GitHub:** [PR #59](https://github.com/seanleecoder/code-tribunal-demo/pull/59)
  replaced the copied workflow with the caller. Its automatic
  [run 43758020213](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/43758020213)
  completed review and critique on all four seats with full health, posted four
  threads and one summary holding state v2, and reported `status: success` with
  no warnings.
- **GitLab:** [MR `!65`](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/65),
  from a protected branch, replaced the child trigger with the direct include
  after `scripts/pipeline_trust.py` accepted it.
  [Pipeline 3071120413](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3071120413)
  gave the same result: four seats in both stages, full health, four threads, a
  state v2 summary, `status: success` and no warnings.

Both runs resolved the shipped default models and efforts. They prove secrets,
variables and posting through the resting deployments; manual dispatch through
an external caller was last exercised in the 3.0 acceptance. `make
demo-preflight` passed before and after. Rollback points: GitHub demo `main` at
`b44ab38`, GitLab demo `main` at `5900e34`, template `main` at `f8ae213`.

## Refuted rollback rehearsal (3.1.0, 2026-11-15)

The isolated [GitHub PR #56](https://github.com/seanleecoder/code-tribunal-demo/pull/56)
and [GitLab MR !62](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/62)
rehearsed the [rollback procedure](../operations.md#rollback-after-refuted-has-been-used)
with deterministic mock reviewers. Earlier authorized `wontfix`, `reopen` and
`resolve` replies preceded `refuted`. On both platforms, refutation resolved the
same thread and survived an unchanged rerun without publication mutations.

The new-image pair came from refuted source
`79377edbd1f4f030acbcadfd0e6fafcc7d0c8890`, published and attested by
[run 43652775495](https://github.com/seanleecoder/code-tribunal/actions/runs/43652775495).
Its exact references were:

- Base: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:3.0-79377edbd1f4f030acbcadfd0e6fafcc7d0c8890@sha256:4914cedd05df55d92ef223b7814491d3cbbf222496cf3d7978f4a49e25f03349`.
- Reviewer: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:3.0-79377edbd1f4f030acbcadfd0e6fafcc7d0c8890@sha256:dbd1d9b40beb0f5f3655c350adffa9c61e13fd97ac39b08bfb96c3509615cf74`.

The final 3.1 runtime source `a7066d427cb0f35e96b0c40f3eacaad9cfaa6634`
only changes the image tag series; application code, schemas and canary drivers
are identical. The exact rollback pair and workflow/template were taken from
[v3.0.1](https://github.com/seanleecoder/code-tribunal/tree/v3.0.1):

- Base: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:3.0-05fe58a65aee5ab1e0538d32b7c183653d4821d4@sha256:948374bdc0ec87d374bc9050573153bf9be0b006f7ee32930b68b7e1267dc649`.
- Reviewer: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:3.0-05fe58a65aee5ab1e0538d32b7c183653d4821d4@sha256:4853a5a738a5ed0ec82d5cbfb2840bae98a2afa568e80853a43dab2dd77ef553`.

| Check | GitHub run | GitLab pipeline | Outcome |
|---|---|---|---|
| Unconverted negative control | [43728953713](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/43728953713) | [3070666219](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3070666219) | Old prepare refused the refuted ledger at schema validation; later stages were skipped and review state was unchanged. |
| Conversion on new images, with the finding absent | [43729504125](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/43729504125) | [3070677330](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3070677330) | A latest authorized `wontfix` replaced both refuted ledger fields. |
| Old-image replay, finding present | [43729826912](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/43729826912) | [3070681204](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3070681204) | Command collection, planning and publication succeeded; `wontfix` persisted with no inline or resolution mutations. |
| Old-image replay, finding absent | [43730089679](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/43730089679) | [3070686783](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/3070686783) | The retained record remained `wontfix`, with no inline or resolution mutations. |

Summary and discussion exports before and after conversion retained note and
author identities outside version control. Both replays retained the issue,
discussion, root-note and state-note identities and resolved state. The negative
controls compared comment bodies, authors, timestamps and state; platform-managed
commit/head metadata changed with the pin commits, as expected. No ledger was
deleted. Both fixtures were closed without merging, their temporary branches
removed and GitLab's scenario variable deleted; demo preflight passed afterwards.
