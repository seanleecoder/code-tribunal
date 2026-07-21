# 1.0 RC live-evidence runbook

Operator runbook for the four outstanding evidence-matrix rows, pinned to the
release-candidate below. It sequences the manual live runs and points each to
its record file. This complements — does not replace — the executable tests
(`make quality`) and the [evidence index](README.md).

## Release candidate under test

> The prior `b674d1e` candidate was invalidated by a GitHub human-command
> authorization defect. Its partial evidence is historical only; every
> release-required probe below must run again against this replacement.

- Source commit: `15d424feea730a04338ed423bf93b8797d807bbc` (`main` HEAD)
- Quality gate: CI `make quality` run **29845398459** — success (SPEC-31/34
  regression tests are inside this run).
- Publish run: **29845398524** — success
  (<https://github.com/seanleecoder/code-tribunal/actions/runs/29845398524>)
- Images (GHCR, tag `1.0-15d424feea730a04338ed423bf93b8797d807bbc`):
  - base `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`
  - reviewer `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`
- Default-model smoke and all other existing evidence records are superseded
  partial evidence. Re-run them with the command-authorization fix and record
  the new run IDs before release.

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

The four runs cannot be executed from CI or a dev container — they need real
scratch consumer projects, runners, protected credentials, and an OpenRouter
key. Prerequisites:

- **GitLab:** a scratch consumer project + a protected template project holding
  `ai-review/ci/` at a fixed SHA; a runner; protected+masked `OPENROUTER_API_KEY`
  and `GITLAB_TOKEN` (`api` scope); **Pipelines must succeed** enabled. Setup:
  [`docs/getting-started/gitlab.md`](../../getting-started/gitlab.md).
- **GitHub:** a scratch consumer repo with the workflow copied from the merged
  P0 release-preparation commit for this candidate (record that exact SHA);
  `OPENROUTER_API_KEY` secret; the `gate` job added as a **required status
  check** in branch protection/ruleset. Setup:
  [`docs/getting-started/github.md`](../../getting-started/github.md).

## The four runs

Runs performed on 2026-07-21 are historical partial results for superseded
images. Repeat every listed probe against this candidate, including probes that
previously passed.

Copy each record, fill Identity/Preconditions, execute, then complete Actual
result / Audit / Verdict.

| # | Run | Record | Key expected outcome |
|---|---|---|---|
| 1 | GitLab hostile-MR (incl. SPEC-31 symlink) | [record-gitlab-hostile-mr.md](record-gitlab-hostile-mr.md) | Trusted composition retained / fail-closed; symlink snapshot rejected; **no token value** in any trace or artifact |
| 2 | GitLab current-image lifecycle | [record-gitlab-current-image.md](record-gitlab-current-image.md) | Create→update→resolve→reopen idempotent; state persists; blocking gate blocks merge |
| 3 | GitHub default-model smoke and current-image lifecycle | [default-model record](record-github-default-model-smoke.md) and [lifecycle record](record-github-current-image.md) | No overrides yield the three shipped defaults with Cursor disabled; same lifecycle + fixed owner commands + stale head; **required check actually blocks** merge |
| 4 | GitHub revision-race / 406 (SPEC-34) | [record-github-revision-failures.md](record-github-revision-failures.md) | Head movement at each prepare boundary fails closed, no mixed bundle; 406 rejected explicitly |

Run order suggestion: **1 → 2 → 3 → 4**. Run 1 exercises the deployment/trust
boundary (highest risk); 2 and 3 the lifecycle; 4 the failure modes. Each is
independent, so they can also be split across GitLab-side and GitHub-side
operators.

### Run 1 — GitLab hostile MR (the SPEC-31 symlink fixture is folded in here)

1. Deploy the chosen production topology (direct or hardened child) in the
   scratch consumer, pinned to the template SHA.
2. From a trusted checkout, audit composition:
   `PYTHONPATH=ai-review/src python scripts/verify_pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`
3. Open an MR from an **unprotected** source branch/fork and run the attack
   matrix in the record (replace jobs/templates, forward variables, override
   trusted image/config, print credential names, forge the gate artifact, and
   add the environment-targeting symlink variants — `/proc/self/environ`,
   relative, parent-escaping, directory, dangling).
4. Confirm each attack is retained/fail-closed and audit **every** trace and
   downloaded artifact for credential *values*.

### Run 2 / Run 3 — Current-image lifecycle (GitLab / GitHub)

Follow the numbered lifecycle steps in each record on a single change request:
create → rerun unchanged → edit body → resolve → reopen → unrelated line
movement → summary fallback → (GitHub: commands, stale head) → forced blocking
finding with enforcement on. Capture pipeline/run/job IDs and platform object
IDs (discussion/note/comment IDs) at every step.

### Run 4 — GitHub revision-race / 406

Force PR head movement at each prepare boundary (checkout-vs-selected, before
diff collection, at manifest finalization) and confirm fail-closed with no
mixed-revision bundle; then drive an oversized raw comparison (HTTP `406`) and
confirm the explicit oversized-diff rejection with no reviewable bundle.

## After all four pass

1. Mark each replacement record `Status: passed` with a scoped verdict.
2. Flip the pending rows in [the evidence matrix](README.md) to scoped passes
   referencing the new run IDs.
3. The consumer templates and active release inputs are already pinned in P0.
   Proceed with the remaining finalization steps: re-run supply-chain + docs
   pin checks, update the changelog/version record, generate
   `release-manifest.json`, then tag `v1.0.0`.

Do not describe 1.0 as "stable" or "credential isolated" until every row is a
scoped pass against this exact RC source and these image digests.
