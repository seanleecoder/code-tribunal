# 1.0 RC live-evidence runbook

Operator runbook for the four outstanding evidence-matrix rows, pinned to the
release-candidate below. It sequences the manual live runs and points each to
its record file. This complements — does not replace — the executable tests
(`make quality`) and the [evidence index](README.md).

## Release candidate under test

- Source commit: `5a24b557e793447fd41b7244c715a134bc1b9592` (`main` HEAD)
- Quality gate: CI `make quality` run **29699507327** — success (SPEC-31/34
  regression tests are inside this run).
- Publish run: **29699507298** — success
  (<https://github.com/seanleecoder/code-tribunal/actions/runs/29699507298>)
- Images (GHCR, tag `1.0-5a24b557e793447fd41b7244c715a134bc1b9592`):
  - base `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:eb8e5d1e9d611f4056216c88a58e10bcb33b758d2fabb7a93b5ddb567d3271b2`
  - reviewer `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:b43f5a14939d76589cfa790a0f54565468b40a411ed9ebd6a4f08844d984863a`

> The `1.0` tag is mutable; **always pull and pin by the `sha256:` digest** in
> consumer templates and when verifying an image.

## Step 0 — Verify the RC images (do this first)

From any machine with registry access (anonymous pulls should work — GHCR public):

```bash
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:eb8e5d1e9d611f4056216c88a58e10bcb33b758d2fabb7a93b5ddb567d3271b2
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:b43f5a14939d76589cfa790a0f54565468b40a411ed9ebd6a4f08844d984863a
# Optional: verify build provenance attestation
gh attestation verify oci://ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:b43f5a14939d76589cfa790a0f54565468b40a411ed9ebd6a4f08844d984863a \
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
- **GitHub:** a scratch consumer repo with the workflow copied from `5a24b55`;
  `OPENROUTER_API_KEY` secret; the `gate` job added as a **required status
  check** in branch protection/ruleset. Setup:
  [`docs/getting-started/github.md`](../../getting-started/github.md).

## The four runs

Copy each record, fill Identity/Preconditions, execute, then complete Actual
result / Audit / Verdict.

| # | Run | Record | Key expected outcome |
|---|---|---|---|
| 1 | GitLab hostile-MR (incl. SPEC-31 symlink) | [record-gitlab-hostile-mr.md](record-gitlab-hostile-mr.md) | Trusted composition retained / fail-closed; symlink snapshot rejected; **no token value** in any trace or artifact |
| 2 | GitLab current-image lifecycle | [record-gitlab-current-image.md](record-gitlab-current-image.md) | Create→update→resolve→reopen idempotent; state persists; blocking gate blocks merge |
| 3 | GitHub current-image lifecycle | [record-github-current-image.md](record-github-current-image.md) | Same lifecycle + commands + stale head; **required check actually blocks** merge |
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

1. Mark each record `Status: passed` with a scoped verdict.
2. Flip the four rows in [the evidence matrix](README.md) from **Outstanding**
   to a scoped pass referencing these records.
3. Proceed with the remaining SPEC-37 release steps: pin the consumer templates
   to these RC digests, generate `release-manifest.json`, re-run supply-chain +
   docs pin checks, update the changelog/version record, then tag `v1.0.0`.

Do not describe 1.0 as "stable" or "credential isolated" until every row is a
scoped pass against this exact RC source and these image digests.
