# Evidence consumer projects

The Candidate Canary and the manual checks in [`RUNBOOK.md`](RUNBOOK.md) need real
consumer projects with real runners and protected credentials. Two long-lived,
operator-controlled, **public** scratch projects serve that purpose. They were used
for the whole 1.0.0 campaign and are the projects to reuse for every subsequent
release — re-creating them from scratch each release wastes hours and loses the
older-format threads that the body-refresh check depends on.

| Platform | Project | Role |
|---|---|---|
| GitHub | <https://github.com/seanleecoder/code-tribunal-demo> | consumer: workflow copy, secrets, repository variables |
| GitLab | <https://gitlab.com/seanleecoder/code-tribunal-demo> (project id `84667714`) | consumer: `.gitlab-ci.yml`, protected/masked variables, runner |
| GitLab | `seanleecoder/code-tribunal-ci-template` (project id `84667707`) | protected template project holding `ai-review/ci/` |

Both consumers are deliberately public and hold no proprietary content, which is why
naming them here does not violate the sanitization rule in
[`README.md`](README.md) — that rule exists to keep *private* consumer coordinates
out of this repository. Never record credential values, session material, or model
content from these projects regardless.

## GitHub consumer — `seanleecoder/code-tribunal-demo`

Public, default branch `main`. Contains a small `src/` + `tests/` Python fixture tree.
The recorded pre-N03 deployment has a single copied workflow,
`.github/workflows/ai-review.yml`. N03's temporary canary branches install a caller
and reusable workflow; the persistent consumer must adopt a release-SHA caller
before the first N03 release, following the
[GitHub installation guide](../getting-started/github.md).

**Already configured — do not rebuild this, only repin it:**

- **Secrets:** `OPENROUTER_API_KEY`, `CURSOR_API_KEY`,
  `AI_REVIEW_GITHUB_RESOLVE_TOKEN`.
- **No branch ruleset and no required status check.** The "Require AI Review gate"
  ruleset (id `19420757`) was deleted on 2026-09-24, and the separate classic branch
  protection on `main`, which also required `gate`, had its required status checks
  removed on 2026-09-27; Code Tribunal informs and never gates merges. Classic
  protection otherwise remains (no force pushes, no deletions). `main` accepts direct pushes, but land a
  workflow adoption as a PR anyway so the new copy runs once before it is merged.
- **Repository variables** (persisted): `AI_REVIEW_MANUAL=false` (the GitHub Chain B procedure re-runs automatic
  `pull_request` runs, which manual mode skips),
  `AI_REVIEW_CURSOR_MODEL=composer-2.5`, and `AI_REVIEW_REVIEWERS=claude, codex, opencode, cursor`.
  Since 2026-10-18 the other seats pin the 2.0.3 shipped defaults explicitly,
  because the consumer still runs an older image:
  `AI_REVIEW_CLAUDE_MODEL=anthropic/claude-haiku-5.5`,
  `AI_REVIEW_CODEX_MODEL=openai/gpt-6-luna`,
  `AI_REVIEW_OPENCODE_MODEL=deepseek/deepseek-v4.1-flash-1017`, and each
  `AI_REVIEW_<SEAT>_EFFORT=low`. The canaries clear the effort variables but
  not the model variables, so keep the model variables equal to the shipped
  defaults; delete all six once the consumer is repinned to 2.0.3.
  The roster variable must include `cursor`: the canonical workflow reads it before exposing
  `CURSOR_API_KEY`. Keep this four-seat resting roster for `make demo-preflight`;
  scope an exact three-seat default smoke to its temporary workflow, then rerun
  preflight after cleanup. The retired per-seat `AI_REVIEW_*_ENABLED` variables and
  `AI_REVIEW_MERGE_GATE_ENABLED` were deleted; a current image fails at config
  load while the merge-gate override is set. The pre-#126 workflow copy still on
  `main` hard-maps that override to `'true'`, so the next adoption from a
  current `R` is what finally removes it.
- **The recorded mock-variable mapping is already in the workflow.** The one-time edit the
  runbook describes has been made: the review and critique steps read
  `AI_REVIEW_LOCAL_MOCK: ${{ vars.AI_REVIEW_LOCAL_MOCK || '0' }}`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK: … || 'false'`,
  `AI_REVIEW_MOCK_SCENARIO: ${{ vars.AI_REVIEW_MOCK_SCENARIO }}`, and each
  `AI_REVIEW_REQUIRE_REAL_*: … || '1'`. So Chain A runs safely with the variables
  unset, and Chain B is driven purely by setting repository variables — no workflow
  commit, which matters because a new commit on the reviewed branch changes the diff
  and moves the mock's selected anchor.
- **Mock variables are currently absent**, which is the intended resting state.
  **Delete them again after every Chain B campaign.** When adopting an image with
  S03, remove the older workflow's `AI_REVIEW_REQUIRE_REAL_*` mappings: the runner
  selects mocks only with both `AI_REVIEW_LOCAL_MOCK=1` and
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`; real adapters always fail on missing
  prerequisites.

**What changes per release after N03:** the caller's release SHA. Both image pins
live in the called workflow. Do not paste the reusable implementation into a
caller file; it has no automatic trigger. Temporary mock checks use the canary's
caller/reusable pair instead of enabling mocks through the production caller.

**Historical resting deployment:** as of 2026-09-27 `main` pinned the 2.0.0 pair
`2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f` (base `sha256:f7028a5a…`, reviewer
`sha256:292142b7…`), adopted by PR #24 (merge `b44ab38`). This is recorded evidence,
not a current pin recommendation. The first major adoption also requires the
[state v2 migration](../operations.md#state-v2-migration).

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

### Threads that must not be deleted

The posted-body refresh check needs a bot-authored thread created by an **older**
image. Preserve GitHub comment `3650942127` on the 1.0.0 Chain B PR #6
(created `2026-07-25 20:13:42`, `issue_id` shared with the GitLab note below). The
1.0.1 image refreshed it to `render-body.v3` and the 2.0.0 candidate to
`render-body.v4`. Run the next refresh with the
[body-format refresh](RUNBOOK.md#body-format-refresh) procedure on
`evidence/chain-b-88bc941`, after confirming its diff is still only the fixture.
Closing the PR is fine; deleting the comment or the branch is not.

## GitLab consumer — `seanleecoder/code-tribunal-demo`

Public, project id `84667714`. Verified present:

- A runner.
- `OPENROUTER_API_KEY` and `GITLAB_TOKEN` (`api` scope), both **protected and
  masked**. Remove the obsolete `AI_REVIEW_CRITIQUE_ENABLED` variable before the next major;
  critique always runs. Delete retired per-seat
  `AI_REVIEW_*_ENABLED` variables; set `AI_REVIEW_REVIEWERS` only to override the
  packaged roster. `AI_REVIEW_MERGE_GATE_ENABLED` is absent, and must stay so: it
  is retired and a current image fails at config load while it is set.
- Protected, unmasked roster, model, and effort variables with the same values
  and caveat as the GitHub consumer's repository variables.
- `only_allow_merge_if_pipeline_succeeds = true` — this is what withholds the merge
  (`detailed_merge_status: ci_must_pass`). `merge_method = merge`.
- **Mock variables absent.** `AI_REVIEW_LOCAL_MOCK`, `AI_REVIEW_ALLOW_LOCAL_MOCK`,
  `AI_REVIEW_REQUIRE_REAL_*`, and `AI_REVIEW_MOCK_SCENARIO` are all gone, which is
  the correct resting state and means 1.0.0's demo-hygiene item was completed here.
  Re-verify before every Chain A run regardless — these are project-wide and sticky,
  and a stale `AI_REVIEW_LOCAL_MOCK=1` silently invalidates a real run.
- The recorded pre-N02 `.gitlab-ci.yml` uses a hardened child trigger with two
  includes at `f8ae2138261d03447f68e5a30ff9ac44ac202bcb` (2.0.0, consumer commit
  `5900e34`). N02's temporary canary branches install a direct include at the
  candidate template SHA, independently of this resting deployment. Migrate the
  persistent consumer using the [GitLab installation guide](../getting-started/gitlab.md)
  before its first N02 adoption; complete the state v2 migration as well.

Before the first N02 campaign, enable the demo's **Allow merge request pipelines
to access protected variables and runners** setting and confirm `main` remains
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
`!12` (hostile, unprotected `hostile/unprotected-88bc941`). 2.0.0 used canary `!21`,
`!22` (Chain B, protected `evidence/chain-b-71dfabc`), and `!23` (hostile,
unprotected `hostile/unprotected-71dfabc`). `!1`–`!9` are the earlier
P0 symlink and hostile-forwarding fixtures. All are closed; none should be deleted.

Mock toggles go in as **project** CI/CD variables, not manual "Run pipeline"
variables: project variables apply to push-triggered `merge_request_event`
pipelines, whereas manual variables are dropped by push-triggered pipelines and
would silently run real. Flip
`AI_REVIEW_MOCK_SCENARIO` by editing the project variable in place and re-triggering
with `POST /projects/84667714/merge_requests/:iid/pipelines` — never pipeline
*retry*, which only re-runs failed jobs and so does not re-drive prepare→post. Project variables are sticky and project-wide: delete them
before any Chain A run and after every Chain B campaign.

Preserve GitLab note `3601861614` on MR `!11` (created `2026-07-25 20:47:29`, updated
`20:59:24`) for the same body-refresh reason as the GitHub comment above. It was
never refreshed, so it still carries the 1.0.0 body format and can prove the GitLab
refresh surface.

## Per-release setup checklist

0. Run `make demo-preflight` (read-only; needs authenticated `gh` and `glab`) and
   fix every `FAIL`. The docstring in `scripts/demo_preflight.py` lists what it
   checks, including leftover mock variables on **both** consumers.
1. Dispatch the [Candidate Canary](../../CONTRIBUTING.md#candidate-canary)
   against the new pair. It needs no repin: it pushes its own temporary branch and
   closes its PR/MR afterwards. Do not repin until it is green.
2. Point the GitHub caller to the reviewed release commit `P`; the first N03
   adoption replaces the old copied workflow. Copy the GitLab template from `P`
   with its three pin variables (`AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`,
   `AI_REVIEW_TRUSTED_IMAGE_SHA`) set to the tested pair.
   Confirm `AI_REVIEW_MERGE_GATE_ENABLED` is still absent from both consumers; it
   is retired and fails the run.
3. Land the GitHub adoption change as a PR. For the first N03 release, perform
   the external SHA-pinned caller check in the
   [release checklist](../development/release-process.md#release-pull-request-checklist).
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
