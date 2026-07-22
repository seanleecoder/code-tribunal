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

> The prior `b674d1e` candidate was invalidated by a GitHub human-command
> authorization defect. Its partial evidence is historical only; every
> release-gating probe below must run again against this replacement.

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

## Step 0 — Verify the RC images (do this first)

Completed for this release candidate: both digest pulls succeeded with an empty
Docker credential directory, both OCI revision labels equal the runtime source,
and both GitHub provenance attestations verified. Update the sanitized
[image-verification record](record-image-publication-verification.md) with this
candidate before final publication.

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
  and `GITLAB_TOKEN` (`api` scope); **Pipelines must succeed** enabled. Setup:
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

1. **One real 3-model panel per platform** proves the default models and adapter
   wiring. Everything else uses the deterministic mock reviewer.
2. **Deterministic mock for every other lifecycle/gate step** — zero tokens, no
   flakiness, and it still drives the *real* platform posting/resolve/reopen/gate
   APIs, which is what those steps exist to prove.
3. **Single reviewer, critique off, cheapest model, minimal diff** for any live
   step that is not the one 3-model smoke.
4. **No dual-digest re-runs of token-bearing rows** — only the reviewer image and
   runtime source affect reviewer behavior; the base image does not.

### The deterministic mock reviewer

`AI_REVIEW_LOCAL_MOCK=1` makes each seat emit a canned, schema-valid finding batch
instead of calling a model (leave the `AI_REVIEW_REQUIRE_REAL_*` flags unset).
`AI_REVIEW_MOCK_SCENARIO` selects the finding set, anchored to the first added
line of the reviewed diff:

| Scenario | Emitted finding | Drives |
|---|---|---|
| `blocking` | one blocker/correctness finding | inline create + blocking gate (with a ≥2 seat quorum, `block_merge=true`, gate exit `7`) |
| `advisory` | one minor/maintainability finding | non-blocking surface at quorum; **below quorum (a single enabled seat) it becomes an FYI on the summary comment** |
| `none` | no findings | unchanged / resolved lifecycle states |
| `default` | historical `records[0]` heuristic | local `make consensus-local` demo |

The batch is finalized by the normal adapter pipeline, so anchors are re-resolved
against the real diff exactly like a real reviewer's output.

> **Scope the mock variables identically across all jobs.** `prepare` stamps the
> effective-config digest into the manifest, and consensus fails closed on
> divergence (SPEC-33). Set `AI_REVIEW_LOCAL_MOCK`, `AI_REVIEW_MOCK_SCENARIO`, and
> any single-seat / critique-off overrides as **pipeline-wide** variables so
> prepare, review, critique, and consensus all see the same configuration.

## The runs

Two tiers. Copy each record, fill Identity/Preconditions, execute, then complete
Actual result / Audit / Verdict.

| # | Run | Record | Tier | Real tokens |
|---|---|---|---|---|
| 1 | Default-model + current-image lifecycle (GitHub) | [default-model record](record-github-default-model-smoke.md) and [lifecycle record](record-github-current-image.md) | release-gating | one 3-model panel (step 1 only) |
| 2 | Current-image lifecycle (GitLab) | [record-gitlab-current-image.md](record-gitlab-current-image.md) | release-gating | one 3-model panel (step 1 only) |
| 3 | GitLab hostile-MR credential/enforcement boundary | [record-gitlab-hostile-mr.md](record-gitlab-hostile-mr.md) | release-gating | none (fails closed before review) |
| 4 | Structural fail-closed confirmations (symlink / revision-race / 406 / gate forgery) | records above + [SPEC-34](../../improvement-specs/spec-34-github-revision-bound-input.md) | regression-covered (optional live) | none |

Run 1/2/3 are the genuinely live-only proofs. Run 4 is confirmation only: its
logic is proven by `make quality` (see the [evidence index](README.md)), so a
live pass is optional and **not** a release gate.

### Runs 1 & 2 — current-image lifecycle (one real panel, then deterministic)

Perform on one change request per platform, capturing run/job IDs and platform
object IDs (comment/discussion IDs) at every step.

1. **Real default-model panel (the only token spend).** Leave all model overrides
   unset, keep all three OpenRouter seats enabled, Cursor disabled,
   `AI_REVIEW_LOCAL_MOCK=0`, `AI_REVIEW_REQUIRE_REAL_*=1`. Create the first inline
   finding and record: Claude `anthropic/claude-haiku-4.5`, Codex
   `openai/gpt-5.4-mini`, OpenCode `google/gemini-3.1-flash-lite`, Cursor `auto`
   skipped, `panel_status: full`. **This one run doubles as the default-model
   smoke — do not run a separate smoke campaign.** Record the OpenRouter-billed
   token/cost for the run (see [operations cost controls](../../operations.md)).
2. **Switch to the deterministic mock for all remaining steps** — set
   `AI_REVIEW_LOCAL_MOCK=1` pipeline-wide and leave the require-real flags unset.
   Drive the lifecycle with `AI_REVIEW_MOCK_SCENARIO`:
   - rerun unchanged (`blocking`) → discussion updated in place, no duplicate;
   - change the finding body → same discussion updated; body-hash recorded;
   - resolve, then reopen → identity preserved (real platform API);
   - push an unrelated line movement (`blocking`) → anchor/identity maintained;
   - summary fallback: run `advisory` with a **single enabled seat** → FYI routed
     to the summary comment;
   - (GitHub) human disposition commands and stale head;
   - force a blocking finding (`blocking`, ≥2 seats) with enforcement on → the
     required check / **Pipelines must succeed** actually blocks merge, and the
     gate agrees with `out/consensus/consensus.json` + `out/post/post_result.json`.

   Every step above exercises the real platform posting/resolve/reopen/gate and
   the real merge-blocking enforcement; only step 1 spends model tokens.

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
   referencing the new run IDs; leave the regression-covered rows classified as
   such.
3. The consumer templates and active release inputs are already pinned in P0.
   Proceed with the remaining finalization: re-run supply-chain + docs pin checks,
   update the changelog/version record, generate `release-manifest.json`, then tag
   `v1.0.0`.

Do not describe 1.0 as "stable" or "credential isolated" until every
release-gating row is a scoped pass against this exact RC source and these image
digests.
