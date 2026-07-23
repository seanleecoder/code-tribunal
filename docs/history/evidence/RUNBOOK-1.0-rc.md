# 1.0 RC live-evidence runbook

Operator runbook for the outstanding evidence-matrix rows, pinned to the
release-candidate below. It sequences the manual live runs and points each to
its record file. This complements — does not replace — the executable tests
(`make quality`) and the [evidence index](README.md).

Its guiding principle is **spend real tokens only on what genuinely requires a
live model or a live platform.** Most matrix logic is already proven by the
regression suite inside `make quality`; those rows are confirmed here at most as
optional wiring checks, not as release gates. See the
[evidence index](README.md) for the per-row classification and the regression
tests that cover each row.

## Release candidate under test

> **Pending replacement.** The `15d424f` source, run IDs, and image digests below
> are **historical**: they predate the `AI_REVIEW_MOCK_SCENARIO` reviewer support
> and the gate `run_id` binding, so the final RC is a **rebuilt** base+reviewer
> pair (see the precondition after Step 0). Treat every concrete value in this
> section and in Step 0 as pending replacement — the operator refreshes the
> source commit, run IDs, digests, and verification status to the rebuilt pair
> before release, and re-runs Step 0 against it.

> The prior `b674d1e` candidate was invalidated by a GitHub human-command
> authorization defect. Its partial evidence is historical only; every
> release-gating probe below must run again against the rebuilt replacement pair.

- Source commit: `15d424feea730a04338ed423bf93b8797d807bbc` (`main` HEAD)
- Quality gate: CI `make quality` run **29845398459** — success (the SPEC-31
  symlink and SPEC-34 revision/406 regression tests are inside this run and are
  the authoritative coverage for those rows).
- Publish run: **29845398524** — success
  (<https://github.com/seanleecoder/code-tribunal/actions/runs/29845398524>)
- Images (GHCR, tag `1.0-15d424feea730a04338ed423bf93b8797d807bbc`):
  - base `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`
  - reviewer `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`
- All existing evidence records are superseded partial evidence. Repeat the
  release-gating probes with the command-authorization fix and record the new
  run IDs before release.

> The `1.0` tag is mutable; **always pull and pin by the `sha256:` digest** in
> consumer templates and when verifying an image.

> **Precondition for the deterministic-mock procedure — rebuild the base image
> first.** The digests pinned above (`15d424f`) predate the
> `AI_REVIEW_MOCK_SCENARIO` reviewer support and the gate `run_id` binding this
> runbook relies on. Both live in `ai-review/src`, which is copied into the
> **base** image (`ai-review/images/base.Dockerfile`); the reviewer image is built
> `FROM` the base and inherits it, and the base runs the `prepare`/`consensus`/
> `post`/`gate` jobs while the reviewer runs `review`/`critique`. So building only
> a reviewer image atop the old base contains neither change. Before the mock
> steps: rebuild the **base** image from a commit that includes them, build the
> **reviewer** `FROM` that exact base, then update **both** digests,
> `runtime_source`, the canonical templates, and `release/release-inputs.json` (see
> the image-pin rotation procedure in [operations](../../operations.md)), and
> re-run Step 0 verification/attestation against the new digests.
> Republishing is an operator/CI action. This commit's gate/mock code ships in the
> product image, so the **final RC is this rebuilt pair** and `15d424f` is
> superseded: run **both** chains (the real smoke and the mock lifecycle) against
> the rebuilt digests, not `15d424f`, so the evidence matches the exact images that
> ship.

## Step 0 — Verify the RC images (do this first)

> The verification recorded here is for the historical `15d424f` pair and is
> **pending replacement**: re-run it against the rebuilt base+reviewer digests
> (below) once they exist, and update the digests in the commands that follow.

Recorded for the historical `15d424f` pair: both digest pulls succeeded with an
empty Docker credential directory, both OCI revision labels equal the runtime
source, and both GitHub provenance attestations verified. Re-verify the **rebuilt**
pair the same way and update the sanitized
[image-verification record](record-image-publication-verification.md) before final
publication.

From any machine with registry access (anonymous pulls should work — GHCR public):

```bash
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d
# Optional: verify build provenance attestation
gh attestation verify oci://ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d \
  --repo seanleecoder/code-tribunal
```

Confirm the digests match the values above before running any smoke.

## What only you (the operator) can do

These runs cannot be executed from CI or a dev container — they need real
scratch consumer projects, runners, protected credentials, and (for the one
model smoke) an OpenRouter key. Prerequisites:

- **GitLab:** a scratch consumer project + a protected template project holding
  `ai-review/ci/` at P0 commit `e1146612b4a86057d145ac14dc532c6a5afde5b7`;
  a runner; protected+masked `OPENROUTER_API_KEY`
  and `GITLAB_TOKEN` (`api` scope); **Pipelines must succeed** enabled; and a
  **protected scratch source branch** for the lifecycle MRs (the protected
  `GITLAB_TOKEN` injects only on protected refs — an unprotected branch withholds
  it and posting fails). Setup:
  [`docs/getting-started/gitlab.md`](../../getting-started/gitlab.md).
- **GitHub:** a scratch consumer repo with the workflow copied from P0 commit
  `e1146612b4a86057d145ac14dc532c6a5afde5b7`;
  `OPENROUTER_API_KEY` secret; the `gate` job added as a **required status
  check** in branch protection/ruleset. Setup:
  [`docs/getting-started/github.md`](../../getting-started/github.md).

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

`AI_REVIEW_LOCAL_MOCK=1` makes each seat emit a canned, schema-valid finding batch
instead of calling a model (an adapter still falls back to a real CLI if any
`AI_REVIEW_REQUIRE_REAL_*` flag is set, so set every one to `0` for Chain B — see
the enabling section below). `AI_REVIEW_MOCK_SCENARIO` selects the finding set,
anchored to the `records[0]`/`data[0]` indexing marker when the diff contains one
(via `_find_indexing_candidate`), otherwise the first added line. **Give the
Chain B diff a stable indexing marker** (the shipped
`ai-review/tests/fixtures/diffs/simple.diff` has one): the marker pins the anchor
to the same line across reruns and unrelated line movements, so finding identity
(`context_hash` → `source_finding_id`) stays stable. The first-added-line fallback
does **not** — inserting a line above shifts which line is "first added", changing
the anchor and opening a new discussion. The scenarios:

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
Chain B you must set `AI_REVIEW_LOCAL_MOCK=1`, set every `AI_REVIEW_REQUIRE_REAL_*=0`,
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
  `AI_REVIEW_REQUIRE_REAL_OPENROUTER/CLAUDE/OPENCODE/CURSOR=0`,
  `AI_REVIEW_MOCK_SCENARIO=<scenario>`) as **project CI/CD variables** for **both**
  topologies. Project variables apply to *every* pipeline in the project —
  including the `merge_request_event` pipelines a `git push` triggers — so the
  push-driven Chain B steps (change body, unrelated line movement) keep the mock;
  they also reach the child in hardened-child mode, where forwarding is disabled
  (`inherit.variables: false`, `forward.pipeline_variables: false`) and manual
  parent variables would not. Project variables are sticky, so heed the warning
  above (Chain A first, or a separate scratch project, or delete them afterward).
  Flip `AI_REVIEW_MOCK_SCENARIO` between Chain B steps by editing the project
  variable in place and re-triggering the pipeline — no workflow commit is needed
  — and remember that value applies project-wide, so any other open MR in the
  scratch project sees the current scenario until you clear it.
  - *Manual "Run pipeline" variables are not sufficient for the full lifecycle.*
    They apply only to that single web/api run and are **dropped by any
    push-triggered pipeline**, so the push-driven steps would silently run real.
    A manual web run also only triggers the DAG when you additionally supply
    `CI_MERGE_REQUEST_IID=<target MR IID>` and select the MR source branch (the
    jobs gate on `web/api && $CI_MERGE_REQUEST_IID`). Use them only for the
    non-push steps if at all; prefer project variables.
- **GitHub** step `env` cannot be overridden by repository variables. Make a
  **one-time** edit to the scratch consumer's copied workflow that maps the
  review/critique step env to variables with **safe defaults** — keep the
  require-real flags, do not delete them:
  `AI_REVIEW_LOCAL_MOCK: ${{ vars.AI_REVIEW_LOCAL_MOCK || '0' }}`,
  `AI_REVIEW_REQUIRE_REAL_OPENROUTER: ${{ vars.AI_REVIEW_REQUIRE_REAL_OPENROUTER || '1' }}`
  (and the same `|| '1'` mapping for `_CLAUDE`/`_OPENCODE`/`_CURSOR`), and
  `AI_REVIEW_MOCK_SCENARIO: ${{ vars.AI_REVIEW_MOCK_SCENARIO }}`. With the variables
  unset, Chain A runs safely (mock off, require-real on); for Chain B set the repo
  variables `AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_REQUIRE_REAL_*=0`, and flip
  `AI_REVIEW_MOCK_SCENARIO` between steps. Do **not** commit a per-scenario workflow
  change — a new commit on the reviewed branch changes the diff and the mock's
  selected anchor. (`workflow_dispatch` inputs mapped the same way are equivalent.)

## The runs

Two tiers. Copy each record, fill Identity/Preconditions, execute, then complete
Actual result / Audit / Verdict.

| # | Run | Record | Tier | Real tokens |
|---|---|---|---|---|
| 1 | Default-model + current-image lifecycle (GitHub) | [default-model record](record-github-default-model-smoke.md) and [lifecycle record](record-github-current-image.md) | release-gating | one 3-model panel (Chain A only) |
| 2 | Current-image lifecycle (GitLab) | [record-gitlab-current-image.md](record-gitlab-current-image.md) | release-gating | one 3-model panel (Chain A only) |
| 3 | GitLab hostile-MR credential/enforcement boundary | [record-gitlab-hostile-mr.md](record-gitlab-hostile-mr.md) | release-gating | none (fails closed before review) |
| 4 | Structural fail-closed confirmations (symlink / revision-race / 406 / gate forgery) | records above + [SPEC-34](../../improvement-specs/spec-34-github-revision-bound-input.md) | regression-covered (optional live) | none |

Run 1/2/3 are the genuinely live-only proofs. Run 4 is confirmation only: its
logic is proven by `make quality` (see the [evidence index](README.md)), so a
live pass is optional and **not** a release gate.

### Runs 1 & 2 — current-image lifecycle (two independent chains per platform)

Run two independent chains per platform. They must **not** share a finding
identity: the real panel emits a model-authored finding whose identity you do not
control, so continuing it with the mock would open a new discussion rather than
update the same one. Capture run/job IDs and platform object IDs at every step.

**Chain A — real default-model smoke (the only token spend).** On its own change
request, leave all model overrides unset, keep all three OpenRouter seats enabled,
Cursor disabled, `AI_REVIEW_LOCAL_MOCK=0`, `AI_REVIEW_REQUIRE_REAL_*=1`. Run one
panel and record: Claude `anthropic/claude-haiku-4.5`, Codex `openai/gpt-5.4-mini`,
OpenCode `google/gemini-3.1-flash-lite`, Cursor `auto` skipped, `panel_status:
full`, and that a finding was posted. **This doubles as the default-model smoke —
do not run a separate smoke campaign.** Record the OpenRouter-billed token/cost
(see [operations cost controls](../../operations.md)). This chain ends here.

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
6. push an unrelated line movement (`blocking`) **outside the marker's context
   window** → the mock re-anchors to the stable `records[0]`/`data[0]` marker, so
   `context_hash`/identity are maintained and post remaps the existing discussion
   in place (no new discussion). Requires the marker in the diff — see the
   deterministic-mock section; the first-added-line fallback would not hold;
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
   `PYTHONPATH=ai-review/src python scripts/verify_pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`.
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

## After the release-gating runs pass

1. Mark each release-gating record `Status: passed` with a scoped verdict, and
   record the per-run token/cost for the one real panel per platform.
2. Flip the pending rows in [the evidence matrix](README.md) to scoped passes
   referencing the new run IDs, including the re-verified image-publication row for
   the rebuilt pair; leave the regression-covered rows classified as such.
3. **Retarget the release inputs to the rebuilt pair (release-blocking).** The
   active `release/release-inputs.json` still points at `15d424f`. Before the RC is
   releasable, update `runtime_source`, both image digests, the canonical template
   pins, the recorded publication and CI run IDs, and the evidence references to the
   rebuilt pair, then re-run `check_release_inputs.py --write-hashes` and
   `make quality`. This is an operator/CI action (it needs the published rebuilt
   digests) and is outside the scope of the repository change that introduced this
   runbook.
4. Proceed with the remaining finalization: re-run supply-chain + docs pin checks,
   update the changelog/version record, generate `release-manifest.json`, then tag
   `v1.0.0`.

Do not describe 1.0 as "stable" or "credential isolated" until every
release-gating row is a scoped pass against the exact rebuilt RC source and image
digests.
