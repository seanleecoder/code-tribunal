# 1.0 RC live-evidence runbook

Operator runbook for the four outstanding evidence-matrix rows, pinned to the
release-candidate below. It sequences the manual live runs and points each to
its record file. This complements — does not replace — the executable tests
(`make quality`) and the [evidence index](README.md).

## Release candidate under test

- Source commit: `963ae5ef8415f6866258ca24c7b5b0b054f58411` (`main` HEAD)
- Quality gate: CI `make quality` run **29819592071** — success (SPEC-31/34
  regression tests are inside this run).
- Publish run: **29819592080** — success
  (<https://github.com/seanleecoder/code-tribunal/actions/runs/29819592080>)
- Images (GHCR, tag `1.0-963ae5ef8415f6866258ca24c7b5b0b054f58411`):
  - base `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1`
  - reviewer `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae`
- Default-model smoke: GitHub Actions run **29824326048** — passed with the
  three shipped OpenRouter defaults and Cursor disabled; see
  [the sanitized record](record-github-default-model-smoke.md).

> The `1.0` tag is mutable; **always pull and pin by the `sha256:` digest** in
> consumer templates and when verifying an image.

## Step 0 — Verify the RC images (do this first)

Completed for this release candidate. Both digest pulls succeeded with an empty
Docker credential directory, both OCI revision labels equal the runtime source,
and both GitHub provenance attestations verified. See the
[sanitized image-verification record](record-image-publication-verification.md).

From any machine with registry access (anonymous pulls should work — GHCR public):

```bash
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae
# Optional: verify build provenance attestation
gh attestation verify oci://ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae \
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
- **GitHub:** a scratch consumer repo with the workflow copied from
  `aa3b171ee65e734fb352d933288c4871de406ce2`;
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
