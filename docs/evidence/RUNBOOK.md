# Live-evidence runbook

Operator runbook for the release-gating evidence-matrix rows. It sequences the
manual live runs and points each to its record file. This complements — does not
replace — the executable tests (`make quality`) and the
[evidence index](README.md).

**This is the durable procedure, not a one-release artifact.** It was executed in
full for 1.0.0 and is the sequence to follow for every subsequent release: fill the
candidate block below with the new runtime source, images, and run IDs, then work
through the steps. Sections that record what a past release actually observed are
marked as such, so a future operator can tell the procedure apart from the results.

**Not every row needs a live re-run every release.** Scope the campaign with the
[change-impact triage table](../development/release-process.md#scoping-the-live-campaign)
before booking operator time: rows whose impact set carries no diff may ship under a
registered evidence waiver instead.

For the accepted release-version grammar and derived notes path, see the
[release version contract](../development/release-process.md#release-version-contract).

Its guiding principle is **spend real tokens only on what genuinely requires a
live model or a live platform.** Most matrix logic is already proven by the
regression suite inside `make quality`; those rows are confirmed here at most as
optional wiring checks, not as release gates. See the
[evidence index](README.md) for the per-row classification and the regression
tests that cover each row.

## Release candidate under test

**Fill this block for the release you are running, then work through the steps.**
Every release-gating probe below must run against the pair named here; a record
bound to an older `R` or digest pair never certifies a later runtime source, however
similar the code. The authoritative coordinates for the release under preparation
live in `release/release-inputs.json` and `release/<release_version>.md` — this block
restates them for the operator.

```text
Runtime source R:  <40-char sha>
Quality gate:      CI `make quality` run <id>
Publish run:       <id>
Base image:        ghcr.io/<org>/code-tribunal/ai-review-base@sha256:<digest>
Reviewer image:    ghcr.io/<org>/code-tribunal/ai-review-reviewer@sha256:<digest>
```

> **Historical: the activated 1.0.0 pair.** Retained because Step 0's example
> commands and several 1.0.0 result notes below reference it. It is **not** the
> current candidate — `release/release-inputs.json` is the 1.0.1 draft
> (`status: draft`, runtime source and digests unset pending rebuild).

> The prior `b674d1e` and `15d424f` candidates are historical provenance only —
> `b674d1e` was invalidated by a GitHub human-command authorization defect and
> `15d424f` predates the mock/gate code. Their partial evidence binds no release.

- Runtime source `R`: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e` (`main` HEAD at 1.0.0)
- Quality gate: CI `make quality` run **30125523924** — success (the SPEC-31
  symlink and SPEC-34 revision/406 regression tests are inside this run and are
  the authoritative coverage for those rows).
- Publish run: **30125524008** — success
  (<https://github.com/seanleecoder/code-tribunal/actions/runs/30125524008>)
- Images (GHCR, tag `1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e`):
  - base `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
  - reviewer `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`
- For **v1.0.0**, the three canonical templates and
  `release/release-inputs.json` were repinned to this pair, so a consumer copied
  from `R` (plus the repin commit) ran exactly these images.
- **All release-gating rows passed against this pair for `v1.0.0`** and are
  recorded with binding `Release-runtime-source` / digest fields; the non-gating
  SPEC-34 revision-failures row was released under a registered waiver, and
  `release/release-inputs.json` was `status: active` for that release. See the
  [evidence matrix](README.md) for the per-row result. For the *next* release,
  replace this identity block and repeat the release-gating probes against the new
  pair — a record bound to `88bc941` does not certify a later runtime source.

> The `1.0` tag is mutable; **always pull and pin by the `sha256:` digest** in
> consumer templates and when verifying an image.

> **Satisfied by the pair above — kept as the procedure for any future rebuild.**
> The `AI_REVIEW_MOCK_SCENARIO` reviewer support and the gate `run_id` binding both
> live in `ai-review/src`, which is copied into the **base** image
> (`ai-review/images/base.Dockerfile`); the reviewer image is built `FROM` the base
> and inherits it, and the base runs the `prepare`/`consensus`/`post`/`gate` jobs
> while the reviewer runs `review`/`critique`. So building only a reviewer image
> atop an older base contains neither change. Whenever the pair is rebuilt: rebuild
> the **base** from a commit that includes the code under test, build the
> **reviewer** `FROM` that exact base, then update **both** digests,
> `runtime_source`, the canonical templates, and `release/release-inputs.json` (see
> the image-pin rotation procedure in [operations](../operations.md)), and re-run
> Step 0 verification/attestation against the new digests. Republishing is an
> operator/CI action. Because the gate/mock code ships inside the product image,
> **both** chains must run against the digests named above, so the evidence matches
> the exact images that ship.

## Step 0 — Verify the RC images (do this first)

> **Done for the final pair** on 2026-07-25. Both subjects resolved anonymously to
> the pinned digests, both OCI revision labels equal `R`, and both provenance
> attestations verified against publication run `30125524008`. Full detail is in
> the [image-verification record](record-image-publication-verification.md). Re-run
> this step only if the pair is rebuilt.

From any machine with registry access (anonymous pulls should work — GHCR public):

```bash
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe
# Verify build provenance attestation (both subjects)
gh attestation verify oci://ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe \
  --repo seanleecoder/code-tribunal
```

To confirm anonymous resolution without touching stored credentials, point
`DOCKER_CONFIG` at a fresh directory containing only `{}` and use
`docker manifest inspect --verbose <ref>` — note that an empty `DOCKER_CONFIG`
also hides CLI plugins, so read the revision labels with the normal config via
`docker buildx imagetools inspect --format '{{json .Image}}' <ref>`.

Confirm the digests match the values above before running any smoke.

## What only you (the operator) can do

These runs cannot be executed from CI or a dev container — they need real
scratch consumer projects, runners, protected credentials, and (for the one
model smoke) an OpenRouter key.

**The consumer projects already exist — reuse them, do not rebuild them.**
[`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md) documents both
`seanleecoder/code-tribunal-demo` projects: what is already configured, the
`evidence/` branch conventions, the threads that must be preserved for the posted-body
refresh check, and the per-release setup checklist. The summary below is the
requirement; that file is the current state.

Prerequisites:

- **GitLab:** the scratch consumer project (`seanleecoder/code-tribunal-demo`,
  project id `84667714`) + a protected template project holding
  `ai-review/ci/` at the templates repinned to `R` (for the 1.0.0 runs this is
  `seanleecoder/code-tribunal-ci-template` at
  `97e05fddf9f5466ccee385344a7aaeac500e4aa2`; the consumer's `.gitlab-ci.yml` must
  reference that same SHA for **both** includes);
  a runner; protected+masked `OPENROUTER_API_KEY`
  and `GITLAB_TOKEN` (`api` scope); **Pipelines must succeed** enabled; and a
  **protected scratch source branch** for the lifecycle MRs (the protected
  `GITLAB_TOKEN` injects only on protected refs — an unprotected branch withholds
  it and posting fails). Setup:
  [`docs/getting-started/gitlab.md`](../getting-started/gitlab.md).
- **GitHub:** the scratch consumer repo (`seanleecoder/code-tribunal-demo`) with
  the workflow copied from `R` and
  repinned to the pair above (copying an older template can carry env keys that `R`
  rejects — the `AI_REVIEW_PANEL_GROUPING_SEMANTIC_*` overrides are one such case);
  `OPENROUTER_API_KEY` secret; the `gate` job added as a **required status
  check** in branch protection/ruleset. Note that a required-check ruleset also
  blocks direct pushes to the default branch, so adopting the workflow itself has
  to go through a PR — run that PR in mock mode so it costs nothing. Setup:
  [`docs/getting-started/github.md`](../getting-started/github.md).

## Cost model: where the tokens go

A full panel is 6–8 real reviewer LLM calls (3–4 reviewers × review + critique).
Only the **review** and **critique** stages call a model; prepare, consensus,
post, and gate are deterministic. The historically expensive procedures ran a
fresh full panel for *every* lifecycle step, and weak-model nondeterminism forced
repeated re-runs. This runbook removes almost all of that spend:

1. **One real 3-model panel per platform** (Chain A) proves the default models and
   adapter wiring. Everything else uses the deterministic mock reviewer.
2. **Deterministic mock for the whole lifecycle/gate chain** (Chain B) — zero
   tokens, no flakiness, and it still drives the *real* platform
   posting/resolve/reopen/gate APIs, which is what those steps exist to prove.
   The two chains use separate change requests and separate finding identities.
3. **Single reviewer, critique off, cheapest model, minimal diff** for any live
   step that is not the one 3-model smoke.
4. **No dual-digest re-runs of token-bearing rows** — validate the real panel once,
   against the single final rebuilt pair, rather than repeating it across candidate
   digests. (The gate/mock code ships in the base image, so both images are rebuilt
   from one commit and validated together — see the precondition above.)

### The deterministic mock reviewer

`AI_REVIEW_LOCAL_MOCK=1` with `AI_REVIEW_ALLOW_LOCAL_MOCK=true` makes each seat
emit a canned, schema-valid finding batch
instead of calling a model (an adapter still falls back to a real CLI if any
`AI_REVIEW_REQUIRE_REAL_*` flag is set, so set every one to `0` for Chain B — see
the enabling section below). `AI_REVIEW_MOCK_SCENARIO` selects the finding set,
anchored to the `records[0]`/`data[0]` indexing marker when the diff contains one
(via `_find_indexing_candidate`), otherwise the first added line. **Give the
Chain B diff a stable indexing marker** (the shipped
`ai-review/tests/fixtures/diffs/simple.diff` has a `records[0]` one) so finding
identity (`context_hash` → `source_finding_id`) is stable across the same-diff
lifecycle steps — create, rerun, body change, resolve, reopen. The
first-added-line fallback is not stable — inserting a line above shifts which
line is "first added", changing the anchor and opening a new discussion.

> **On GitHub, the Chain B fixture must ADD a file — deliberately.** At
> `R = 88bc941` (the 1.0.0 runtime) this was impossible: GitHub's prepared
> `mr.diff` renders an added file with `--- /dev/null`; `parse_unified_diff` kept
> that verbatim, and `context_hash_from_unified_diff` normalized the parsed path
> while scanning, raising `absolute paths are not allowed: /dev/null`. Finalization
> caught that and **dropped the finding**, so every seat reported
> `raw_finding_count=1, accepted_finding_count=0, usable_for_resolution=false`,
> consensus exited 3, and `post`/`gate` were skipped. Observed live in GitHub run
> `30172413739`. It affected **real reviewers too**, not just the mock: the raise
> happened while scanning the diff, before the anchor's own paths were compared,
> and it triggered on an added or deleted file, or on a file appearing *after* one
> in the diff. Every 1.0.0 fixture was therefore modify-only.
>
> **The anchor fix landed after `v1.0.0` and ships in 1.0.1.** So the GitHub Chain B
> fixture must now **add** a file, and step 1 of the chain asserts
> `accepted_finding_count == raw_finding_count` with a posted inline discussion.
> That assertion is the release's headline claim — do not substitute a modify-only
> fixture, because no live run has ever exercised this path green.
>
> **Platform asymmetry still holds and still drives fixture choice.** GitLab's
> prepared diff emits `--- a/<path>` for added files rather than the sentinel, so it
> never hit the defect and its fixture proves something different. Choose fixtures
> per platform deliberately rather than assuming symmetry.

> **Unrelated line movement is regression-covered, not a token-free mock live
> step.** Keeping finding identity across a line movement is cross-revision remap
> behavior: a real push advances the head SHA and regenerates the served unified
> diff (with the platform's own limited context), which the mock cannot faithfully
> reproduce — and identity depends on the `context_hash` ±6 new-side window, which
> a hand-shaped fixture would only approximate. The **internal** remap contract —
> finding identity is preserved and the persisted state anchor follows the marker,
> so the run updates the one existing discussion instead of opening a duplicate — is
> proven by
> `integration/test_post_gate_e2e.py::test_line_movement_across_revisions_remaps_to_same_discussion`
> (two independently prepared revisions, each with its own head SHA, diff digest, and
> run_id) plus the `test_anchors` remap and `test_post.py` run-to-run upsert unit
> tests. What that test does **not** prove is *platform-visible* re-anchoring:
> updating an existing GitHub/GitLab comment rewrites its body, not its original diff
> position, and `post.py` marks visible placement as requiring separate live
> validation. So the visible re-anchoring of a moved comment stays a documented,
> **live-optional** confirmation (not release-gating); the internal remap is
> regression-covered.

The scenarios:

| Scenario | Emitted finding | Drives |
|---|---|---|
| `blocking` | one blocker/correctness finding | inline create + blocking gate (with a ≥2-seat quorum, `block_merge=true`, gate exit `7`) |
| `blocking_alt` | same identity as `blocking` (same title, category, anchor), different body | the changed-body in-place update: the existing discussion is updated, `body_hash` changes, no new discussion is created |
| `advisory` | one minor/maintainability finding | a **non-blocking inline surface** finding at quorum; the gate passes |
| `none` | no findings | absence-based resolution / withdrawal of a previously posted finding (NOT an unchanged rerun) |
| `default` | historical `records[0]` heuristic | local `make consensus-local` demo |

The batch is finalized by the normal adapter pipeline, so anchors are re-resolved
against the real diff exactly like a real reviewer's output.

> The below-quorum **FYI/summary-comment** path and the **inline-unmappable
> summary fallback** are not reachable through these uniform mock scenarios (the
> mock emits identical findings across seats, which always group to quorum, and
> config validation rejects a `votes_required`/enabled-seat mismatch). Both are
> **regression-covered** (`integration/test_post_gate_e2e.py` FYI cases and
> `test_post.py` summary-fallback cases); do not attempt a single-seat FYI live
> run.

**Enabling the mock in the scratch consumer (Chain B only).** The shipped templates
hardcode `AI_REVIEW_LOCAL_MOCK: "0"` and `AI_REVIEW_REQUIRE_REAL_*: "1"`. To run
Chain B you must set `AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
set every `AI_REVIEW_REQUIRE_REAL_*=0`,
and set `AI_REVIEW_MOCK_SCENARIO`; the mechanism differs by platform. These are
**adapter controls** that only affect review/critique behavior — they are *not*
part of the prepare-stamped effective-config digest — so set them consistently on
the **review and critique** jobs (project-wide is simplest). If you also change a
config-affecting override for Chain B (`AI_REVIEW_CRITIQUE_ENABLED`,
`AI_REVIEW_<R>_ENABLED/MODEL/EFFORT`), that *does* feed the effective-config digest,
so scope it identically across **all** jobs or consensus fails closed on divergence
(SPEC-33). Never edit a production template.

> **Sticky-variable warning — do not let the mock leak into Chain A.** Chain A is
> the *real* smoke and must run with the mock off and require-real on. Persisted
> settings (GitLab project variables, GitHub repository variables) survive between
> runs, so a mock left enabled would silently turn a later Chain A into a mock run.
> Run **Chain A first** (before setting any mock variable), or use a **separate
> scratch project/repo** for Chain B, or delete the mock variables before any Chain
> A run. The GitHub mapping below defaults to safe values when the variables are
> unset.

- **GitLab — protected source branch + project variables.**
  Any GitLab lifecycle run (Chain A or B) must open its MR from a **protected
  scratch source branch**: the required `GITLAB_TOKEN` is masked+protected and
  powers prepare/discussions/state/commands, and protected CI/CD variables inject
  **only on protected refs**. On an unprotected feature branch the protected
  `GITLAB_TOKEN` (and any variable you marked Protected) is withheld, so prepare
  and posting fail outright — credentials are unavailable. (Mock toggles set as
  *unprotected* project variables would still inject on any branch, but the run
  fails anyway without the token, so the protected branch is required regardless.)
  With the branch protected, set the mock
  toggles (`AI_REVIEW_LOCAL_MOCK=1`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
  `AI_REVIEW_REQUIRE_REAL_OPENROUTER/CLAUDE/OPENCODE/CURSOR=0`,
  `AI_REVIEW_MOCK_SCENARIO=<scenario>`) as **project CI/CD variables** for **both**
  topologies. Project variables apply to *every* pipeline in the project —
  including the `merge_request_event` pipelines a `git push` triggers (opening the
  MR, and any commit pushed for a lifecycle step) — so the mock stays active across
  the whole chain no matter how a pipeline is triggered; they also reach the child
  in hardened-child mode, where forwarding is disabled
  (`inherit.variables: false`, `forward.pipeline_variables: false`) and manual
  parent variables would not. Project variables are sticky, so heed the warning
  above (Chain A first, or a separate scratch project, or delete them afterward).
  Flip `AI_REVIEW_MOCK_SCENARIO` between Chain B steps by editing the project
  variable in place and re-triggering the pipeline — no workflow commit is needed
  — and remember that value applies project-wide, so any other open MR in the
  scratch project sees the current scenario until you clear it.
  - *Manual "Run pipeline" variables are not sufficient for the full lifecycle.*
    They apply only to that single web/api run and are **dropped by any
    push-triggered pipeline**, so any pipeline triggered by a push (the MR's own
    commits, or a re-trigger by push) would silently run real.
    A manual web run also only triggers the DAG when you additionally supply
    `CI_MERGE_REQUEST_IID=<target MR IID>` and select the MR source branch (the
    jobs gate on `web/api && $CI_MERGE_REQUEST_IID`). Use them only for the
    non-push steps if at all; prefer project variables.
- **GitHub** step `env` cannot be overridden by repository variables. Make a
  **one-time** edit to the scratch consumer's copied workflow that maps the
  review/critique step env to variables with **safe defaults** — keep the
  require-real flags, do not delete them:
  `AI_REVIEW_LOCAL_MOCK: ${{ vars.AI_REVIEW_LOCAL_MOCK || '0' }}`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK: ${{ vars.AI_REVIEW_ALLOW_LOCAL_MOCK || 'false' }}`,
  `AI_REVIEW_REQUIRE_REAL_OPENROUTER: ${{ vars.AI_REVIEW_REQUIRE_REAL_OPENROUTER || '1' }}`
  (and the same `|| '1'` mapping for `_CLAUDE`/`_OPENCODE`/`_CURSOR`), and
  `AI_REVIEW_MOCK_SCENARIO: ${{ vars.AI_REVIEW_MOCK_SCENARIO }}`. With the variables
  unset, Chain A runs safely (mock off, require-real on); for Chain B set the repo
  variables `AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
  `AI_REVIEW_REQUIRE_REAL_*=0`, and flip
  `AI_REVIEW_MOCK_SCENARIO` between steps. Do **not** commit a per-scenario workflow
  change — a new commit on the reviewed branch changes the diff and the mock's
  selected anchor. (`workflow_dispatch` inputs mapped the same way are equivalent.)

## Operator mechanics

Read this before driving a chain. Each item cost real time to discover during the
1.0.0 runs and applies to every release.

**Re-drive a step without a new change request.** This is the biggest efficiency win.

- **GitHub:** `gh run rerun <run-id>` re-runs the *same* commit as a new attempt, and
  a repository-variable change is picked up because variables are read at job start.
  One pull request therefore covers the whole lifecycle — 1.0.0 used attempts 1–7 of
  run `30173073036`, changing only `AI_REVIEW_MOCK_SCENARIO` between them. Fetch a
  specific attempt's artifacts/jobs with
  `gh api repos/<repo>/actions/runs/<id>/attempts/<n>/jobs`.
- **GitLab:** `POST /projects/:id/merge_requests/:iid/pipelines` creates a fresh MR
  pipeline on the same head. Do **not** use pipeline *retry* — it only re-runs failed
  jobs, so it re-runs the gate rather than re-driving prepare→post.

**Added-file diffs differ by platform.** GitHub renders an added file as
`--- /dev/null`; GitLab renders `--- a/<path>`. A fixture that behaves one way on one
platform can behave differently on the other. Choose fixtures per platform
deliberately rather than assuming symmetry.

**Do not force-update a source branch to its base.** Doing so leaves the pull request
with zero commits, GitHub auto-closes it, and subsequent pushes then fire no
`pull_request` event — so no pipeline starts and the chain appears to hang. Open a
fresh change request instead of rewinding one.

**Deleting the mock variables is a safety mechanism, not tidiness.** With them absent,
the workflow defaults restore `AI_REVIEW_REQUIRE_REAL_*=1`, so a run that somehow
still reached the mock adapter **fails closed** instead of quietly producing fake
"real" evidence. Delete them before any Chain A run and confirm they are gone.

**Audit with the committed scanner, not an ad-hoc grep:**

```bash
python scripts/scan_evidence_leaks.py <artifact-dirs…> <trace-dirs…>
# operator-only, compares against configured secret values without exposing them in argv:
python scripts/scan_evidence_leaks.py <dirs…> --exact-value-file /path/to/secrets
```

### Carried coverage gaps

The inherited list of paths no live run has exercised. **Updating this table is a
step in the release sequence:** each release removes what it closed and adds what it
discovered, so the next release starts here instead of rediscovering it.

| Gap | Why it is unproven | How to close it |
|---|---|---|
| Added-file lifecycle | blocked at 1.0.0 by the `/dev/null` anchor defect, so every fixture was modify-only. The fix ships in 1.0.1; shipping it does not by itself close the gap | run Chain B with an **adding** fixture on GitHub and assert `accepted_finding_count == raw_finding_count` plus a posted inline discussion — queued as the 1.0.1 headline run |
| `render-body.v3` refresh of a pre-v3 thread | the format changed after `v1.0.0`, so no run has re-reviewed a thread authored by an older image | re-review a change request whose bot thread predates v3 (the 1.0.0 threads GitHub comment `3650942127` / GitLab note `3601861614` still exist) and assert `updated_discussions=1`, `created=0`, same `issue_id` |
| Below-quorum FYI / summary comment | the mock emits identical findings on every seat, so quorum is always reached | needs a per-seat mock scenario (single-seat emission); see SPEC-41 |
| Inline-unmappable summary fallback | the mock always anchors successfully | needs a mock scenario emitting a deliberately unmappable anchor |
| Live symlink containment variant | the GitLab commits API cannot create a `120000` tree entry, and SSH push was unavailable | **reuse the existing `evidence/p0-symlink-*` branches**, which already carry the fixtures — no push required |
| GitLab fork-based MR | the hostile probe used an unprotected in-project branch | open the probe from a fork |
| Protected-ref insider | not attempted | out of scope unless the threat model changes |
| Cursor reviewer | the route was outside the 1.0.0 release matrix | [the supplemental record](record-cursor-real-runs.md) is historical supporting evidence bound to older coordinates. Cursor is a supported seat and needs nothing further to enable; a release shipping it on the default roster would want its own gating row, and each `cursor-agent.pin` bump needs the publisher-side permission smoke (Run 6) |
| OpenRouter token/cost | no artifact carries a token or cost field | read the dashboard, or add usage capture to the adapters |

## The runs

Two tiers. Copy each record, fill Identity/Preconditions, execute, then complete
Actual result / Audit / Verdict.

| # | Run | Record | Tier | Real tokens |
|---|---|---|---|---|
| 1 | Default-model + current-image lifecycle (GitHub) | [default-model record](record-github-default-model-smoke.md) and [lifecycle record](record-github-current-image.md) | release-gating | one 3-model panel (Chain A only) |
| 2 | Current-image lifecycle (GitLab) | [record-gitlab-current-image.md](record-gitlab-current-image.md) | release-gating | one 3-model panel (Chain A only) |
| 3 | GitLab hostile-MR credential/enforcement boundary | [record-gitlab-hostile-mr.md](record-gitlab-hostile-mr.md) | release-gating | none (fails closed before review) |
| 4 | Structural fail-closed confirmations (symlink / revision-race / 406 / gate forgery) | records above + SPEC-34 | regression-covered (optional live) | none |
| 5 | Cursor real-run adapter and critique (historical) | [Cursor supplemental record](record-cursor-real-runs.md) | experimental / non-release | two historical real runs; Cursor-specific route |
| 6 | Cursor model-specific evidence | a new supplemental record | optional; not a gate | final-image real run and permission smoke |

Run 1/2/3 are the genuinely live-only proofs. Run 4 is confirmation only: its
logic is proven by `make quality` (see the [evidence index](README.md)), so a
live pass is optional and **not** a release gate. Run 5 is historical supporting
evidence; Run 6 is optional and gates nothing.
The ask-mode/blocking contract is a product decision to record when it is made.

### Runs 1 & 2 — current-image lifecycle (two independent chains per platform)

Run two independent chains per platform. They must **not** share a finding
identity: the real panel emits a model-authored finding whose identity you do not
control, so continuing it with the mock would open a new discussion rather than
update the same one. Capture run/job IDs and platform object IDs at every step.

**Chain A — real default-model smoke (the only token spend).** On its own change
request, leave all model overrides unset, keep all three OpenRouter seats enabled,
Cursor disabled, `AI_REVIEW_LOCAL_MOCK=0`, `AI_REVIEW_REQUIRE_REAL_*=1`. Run one
panel and record: Claude `anthropic/claude-haiku-4.5`, Codex `openai/gpt-5.6-luna`,
OpenCode `google/gemini-3.5-flash-lite`, Cursor `auto` skipped, `panel_status:
full`, and that a finding was posted. **This doubles as the default-model smoke —
do not run a separate smoke campaign.** Record the OpenRouter-billed token/cost
(see [operations cost controls](../operations.md)). This chain ends here.

**Chain B — deterministic mock lifecycle (zero tokens).** On a second change
request, enable the mock via the platform-specific mock enablement above (GitLab
project variables / GitHub workflow-variable mapping). Every step drives the real
platform posting/state/resolve/reopen/gate APIs on **one mock finding identity**;
model quality is irrelevant, so no tokens are spent:

1. create (`blocking`) → one inline discussion at the mapped line;
2. rerun unchanged (`blocking`, same commit) → same discussion, `post_result`
   `updated_discussions=0` and `skipped_unchanged>=1`, **no duplicate**;
3. change body (`blocking_alt`) → **same discussion updated in place**,
   `updated_discussions=1`, recorded `body_hash` changes, no new discussion
   (identity is preserved because body is excluded from finding identity);
4. resolve → drive resolution with a `/ai-review wontfix` disposition command
   (this is one resolution mechanism — a `/ai-review resolve` command or the native
   platform resolve API are equivalent alternatives), then rerun `blocking`; expect
   `resolved_discussions>=1`, the thread marked resolved, and the state note to
   persist the disposition on a further unchanged `blocking` rerun (same discussion
   id, `skipped_unchanged>=1`);
5. reopen → clear the disposition via the platform's native resolve/reopen API (or
   a `/ai-review reopen` command), then rerun `blocking`; expect the same
   discussion active again with identity preserved (no new discussion created);
6. unrelated line movement — **internal remap regression-covered; visible
   placement optional live** (see the note above): the cross-revision remap that
   keeps finding identity and moves the persisted state anchor (so the one existing
   discussion is updated, not duplicated) is proven by the two-revision e2e and the
   `test_anchors`/`test_post` remap tests. The *platform-visible* re-anchoring of the
   moved comment is not reproduced by the mock and remains a live-optional
   confirmation, not release-gating; skip it as a token-free step and confirm live
   only if convenient;
7. (GitHub) exercise the stale-head no-op (push a new head mid-run) → post/gate
   detect the superseded revision and do not act (disposition commands are already
   covered by steps 4–5);
8. force the blocking gate (`blocking`, ≥2 seats) with enforcement on → the
   required check / **Pipelines must succeed** actually blocks merge, and the gate
   agrees with `out/consensus/consensus.json` + `out/post/post_result.json`.

The `advisory` scenario (non-blocking inline surface, passing gate) may be run as
an extra state; the FYI/summary-comment and inline-unmappable fallback paths are
regression-covered and are not part of this live chain.

### Run 3 — GitLab hostile-MR credential & enforcement boundary

This run fails closed in `prepare` and never reaches a reviewer, so it spends no
tokens. Exercise the genuinely live-only probes:

1. Open an MR from an **unprotected** source branch/fork → protected
   `OPENROUTER_API_KEY`/`GITLAB_TOKEN` are withheld; prepare fails closed and the
   uploaded artifact contains only an empty `inputs/` tree.
2. From a trusted checkout, audit composition with
   `python scripts/pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`.
3. Attempt the override/forgery probes that touch a credential-bearing boundary
   (template/job replacement, trusted image/config override, forged `out/gate/*`).
   Confirm the trusted composition is retained or the pipeline fails closed, and
   audit every trace/artifact for credential *values*.

The SPEC-31 symlink variants and the SPEC-33 forged-gate integrity binding are
regression-covered (`ai-review/tests/unit/test_input_bundle.py` and
`test_gate.py`); confirm at most one representative symlink variant live and rely
on the regression suite for the rest.

### Run 4 — structural fail-closed confirmations (optional, not release-gating)

The revision-race boundaries (checkout-vs-selected, before-diff, and
manifest-finalization), the oversized-diff HTTP 406 rejection, and the symlink
classes are **fully covered** by `make quality` (`test_input_bundle.py`,
`test_github_platform.py`). Two of these were never reproducible live because the
race windows are milliseconds wide. Treat any live attempt as optional wiring
confirmation and record it as such; do not block the release on reproducing a
timing race that the regression tests already prove fail-closed.

### Run 5 — Cursor experimental reviewer evidence (non-release)

The [Cursor supplemental record](record-cursor-real-runs.md) captures the
real-project GitLab pipeline and the GitHub dogfood run. Confirm in the review
and critique artifacts that `adapter_status: success`, accepted findings equal
raw findings, `usable_for_resolution: true`, and the panel lists Cursor as a
successful reviewer. These checks establish real-route wiring and artifact
validity only.

These runs recorded `model: auto`, so they say nothing about any particular
model; ordinary review success is also not permission-denial evidence.

### Run 6 — Cursor model-specific evidence (optional)

**Not an enablement gate.** SPEC-21 is closed and Cursor is a supported peer seat,
so nothing here is required before a consumer enables it. This is the procedure for
evidence bound to one *specific* Cursor model and reviewer image — which is why it
pins an exact slug where ordinary operation may use `auto`.

The publisher runs its permission-smoke half for each `cursor-agent.pin` bump,
because that smoke is the only check that a newly pinned CLI honours the `Shell(*)`
deny policy (see [SUPPLY_CHAIN.md](../../ai-review/images/SUPPLY_CHAIN.md)). Run the
rest when a deployment needs model-specific evidence, or when a release intends to
ship Cursor on the default roster and wants a gating row of its own. The Run 5 records cannot serve: they used an older
reviewer image and reported `model: auto`.

1. Freeze `R`, build and attest the final base/reviewer pair, validate
   `cursor-agent.pin`, and record immutable digests/provenance.
2. Resolve the exact model with `cursor-agent --list-models`, set the controlled
   `AI_REVIEW_CURSOR_MODEL`/YAML value, and record the exact slug without secrets
   or model content.
3. Record the ask-mode decision. If prompt-bundle-only is accepted, state that
   explicitly; otherwise change the invocation and repeat the read/permission
   validation. If blocking behavior is required, use a blocking fixture and
   verify the required check genuinely blocks.
4. Run the parameterized permission smoke with a real `CURSOR_API_KEY` against
   the exact final reviewer image. A missing key or a `Skipping Cursor
   permission smoke` notice is not a pass.
5. Run the fresh real-key fixture review/critique under the chosen contract and
   record exact model, counts, config digest, runtime/image coordinates,
   provenance, job IDs, and consensus/post/gate outcomes without secrets or model
   text.
6. Add the sanitized supplemental record only after all required paths are scoped
   `Status: passed` against the same `R` and final image pair. Repin the
   configuration and publisher workflow to the same exact model slug. Do not add
   this record to release inputs. Cursor may remain off the default roster as an
   accepted opt-in seat; completing this gate is required only before adding
   Cursor to a roster.

## After the release-gating runs pass

> **Completed for 1.0.0** on 2026-07-25 against `R = 88bc941` (release commit
> `3ad443e`, tag `v1.0.0`). The steps below are retained as the reusable sequence
> for 1.0.1; the parenthetical notes record how 1.0.0 satisfied each.

1. Mark each release-gating record `Status: passed` with a scoped verdict, and
   record the per-run token/cost for the one real panel per platform. (1.0.0: all
   six cited records stamped with `Release-runtime-source` and both digests; the
   non-gating SPEC-34 row carries a registered `Release-evidence-waived` reason
   instead. Token/cost is **not** in any artifact — read it from the OpenRouter
   dashboard or leave it unasserted, as 1.0.0 did.)
2. Flip the pending rows in [the evidence matrix](README.md) to scoped passes
   referencing the new run IDs, including the re-verified image-publication row for
   the rebuilt pair; leave the regression-covered rows classified as such.
3. **Retarget the release inputs to the pair under test (release-blocking).**
   Update `runtime_source`, both image digests, the canonical template pins, the
   recorded publication and CI run IDs, and the evidence references together, then
   re-run `make quality`. This is an
   operator/CI action because it needs the published digests. (1.0.0: publication
   run `30125524008`, CI run `30125523924`, base `sha256:f2a433ac…`, reviewer
   `sha256:2fd84c43…`. Remember the **three** GitLab pin variables and **both**
   byte-identical GitHub workflow copies, plus the consumer/template projects used
   for evidence — a stale template pin means the evidence exercised the wrong
   images.)
4. Audit for credential leakage across every retained artifact and trace with
   `python scripts/scan_evidence_leaks.py <dirs…>` and record its exact scope and
   limitations in the records. (1.0.0: 438 files / 5.7 MB, zero hits; the
   exact-value scan was left as an operator sign-off item.)
5. Proceed with the remaining finalization: re-run supply-chain + docs pin checks,
   update the changelog/version record, generate and validate the external
   manifest, then tag. **The tag target is constrained** — do not squash-merge the
   release commit, and either tag `P` exactly or rebuild the manifest against the
   merge commit; see the tagging section of the release notes.

Do not describe 1.0 as "stable" or "credential isolated" until every
release-gating row is a scoped pass against the exact rebuilt RC source and image
digests.
