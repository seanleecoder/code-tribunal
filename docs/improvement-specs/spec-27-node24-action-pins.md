# SPEC-27 — Upgrade GitHub Actions pins off the deprecated node20 runtime

- **Severity:** Medium (deprecation warnings on every run; future forced breakage) · **Effort:** S · **ROI rank:** 5 (pre-1.0)
- **Depends on:** none.

## Why

Pipelines emit "Node.js 20 actions are deprecated" warnings. The source is
**not** the reviewer image (its `node:22` CLI build stage is current LTS) but
the node20-runtime GitHub Action pins:

- `actions/checkout` v4.3.0 (`08eba0b2…`) — `ci.yml`, `ai-review.yml`,
  `ai-review/ci/review.github-actions.yml`
- `actions/setup-python` v5.6.0 — `ci.yml`
- `actions/github-script` v7.0.1 — AI Review "Resolve pull request" step
- `actions/upload-artifact` v4.6.2 / `actions/download-artifact` v4.3.0 —
  all workflows
- `.github/workflows/publish-ai-review-images.yml` already uses node24
  checkout v6.0.3 (`df4cb1c0…`) in two places; its other pins need checking.

## Scope

**In:** action pins in `.github/workflows/ci.yml`,
`.github/workflows/ai-review.yml`, `ai-review/ci/review.github-actions.yml`,
`.github/workflows/publish-ai-review-images.yml`; the
`APPROVED_ACTION_PINS` registry in `scripts/check_supply_chain_pins.py`.

**Out:** `ai-review/images/reviewer.Dockerfile` `node:22` stage (current LTS,
not the warning source — optional bump to node:24 LTS is a separate,
non-urgent follow-up); GitLab templates (no actions).

## Implementation

1. Bump every node20-runtime action to the current node24-runtime major,
   pinned by **full commit SHA with a trailing version comment** (repo
   convention). Targets at spec time — implementer must resolve the latest
   patch SHA for each and verify the runtime is node24:
   - `actions/checkout` → v6.x (v6.0.3 SHA `df4cb1c069e1874edd31b4311f1884172cec0e10` is already in the approved registry; reuse or bump).
   - `actions/setup-python` → latest v6.
   - `actions/github-script` → latest v8 (verify the inline script still
     works: v8 keeps the `github`/`core`/`context` globals; the script in
     "Resolve pull request" uses only those).
   - `actions/upload-artifact` → latest node24 major (v5+); verify behavior
     of existing inputs (`name`, `path`, `if: always()`).
   - `actions/download-artifact` → latest node24 major (v6+); verify
     `pattern` + `merge-multiple` inputs are still supported (they are in
     v5+/v6+, but confirm against the chosen version).
   - `actions/attest` in the publish workflow: check its runtime; bump if
     node20.
2. `scripts/check_supply_chain_pins.py`: replace `APPROVED_ACTION_PINS` with
   exactly the new (action, sha) → version set; **remove** superseded
   entries so a stale pin anywhere fails the check.
3. Keep `ai-review/ci/review.github-actions.yml` and
   `.github/workflows/ai-review.yml` byte-identical (existing check enforces
   this — update both).

## Acceptance criteria

- No "Node.js 20 actions are deprecated" annotations on CI, AI Review, or
  publish workflow runs.
- `python scripts/check_supply_chain_pins.py` passes and would fail on any
  reintroduced node20 pin (stale entries removed from the registry).
- A manual `workflow_dispatch` of AI Review still resolves PR metadata
  (github-script v8) and the artifact hand-offs between jobs still work.

## Tests

- `pytest ai-review/tests/unit/test_check_supply_chain_pins.py
  ai-review/tests/unit/test_ci_template.py` green with the new pins.
- CI run on the PR: zero node deprecation annotations (manual check of the
  run summary).

## Risk / rollback

Major-version action bumps can change defaults (e.g. checkout v5+
`persist-credentials`; the repo already sets it explicitly where it
matters). Artifact actions v4→v5/v6 keep the v4 artifact backend; verify the
cross-job download in a real run before merge. Rollback = revert the pins
and registry together.
