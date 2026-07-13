# Phase 3 Implementation Plan — Platform + Scale

> Current status: Steps 1–3 (SPEC-15a, SPEC-15b, and SPEC-16) are complete on
> `main`. Steps 4–7 (SPEC-17 and SPEC-18) remain active roadmap work.

This plan turns `phase-3-platform-scale.md` into a landing sequence that expands
Code Tribunal beyond its initial GitLab implementation without losing the
deterministic reducer and E2E safety net established in Phases 1–2. It assumes
SPEC-12, SPEC-13, and SPEC-14 have landed: the post/gate path has hermetic E2E
coverage, reducer-domain types exist, and `post_consensus` has a clean seam for
platform adapters.

## Goals

- Introduce a platform port so posting, input-bundle preparation, and gate logic
  can target GitLab or GitHub through the same product contract.
- Add GitHub pull-request review support as an additive adapter while keeping the
  GitLab path behavior-identical.
- Make reviewer image builds reproducible by pinning mutable npm, Python, base
  image, and GitHub Actions inputs.
- Reduce default cost/latency risk with a measured, config-gated adaptive panel
  that can fall back to the full panel.
- Either wire or remove budget/Jira/reserved configuration so the shipped config
  no longer presents inert knobs as delivered behavior.
- Draw a package boundary around the deterministic reducer and publish the
  finding/critique/consensus schemas as a versioned interoperability surface.

## Non-goals

- Flipping production users from GitLab to GitHub by default.
- Rewriting consensus policy while adding platform support; the reducer contract
  should stay stable except for explicitly versioned schema/package work.
- Shipping partially trusted GitHub Actions guidance that runs secrets from
  attacker-controlled workflow YAML.
- Treating adaptive mode as the default until the SPEC-12 corpus proves blocker
  recall is preserved.
- Publishing the reducer package before the code is free of SCM/HTTP and platform
  dependencies.

## Recommended landing order

| Step | Spec | Branch / PR theme | Rationale | Primary validation |
|---:|---|---|---|---|
| 0 | Prerequisite | Confirm Phase 2 seams and safety net | SPEC-15 and SPEC-18 depend on typed, decomposed, tested reducer/post boundaries. | SPEC-12 E2E/golden plus mypy target pass unchanged. |
| 1 | SPEC-15a | Introduce `ReviewPlatform` and move GitLab behind it | Create the port first as a behavior-preserving refactor before adding GitHub-specific behavior. | GitLab E2E/golden unchanged; `post.py` and `input_bundle.py` no longer import GitLab symbols directly. |
| 2 | SPEC-15b | Add GitHub adapter and safe Actions integration docs | Add the new market adapter once the contract is stable and tested against fakes. | Fake-GitHub E2E drives consensus→post→gate and creates inline PR review comments. |
| 3 | SPEC-16 | Supply-chain pinning | Independent hardening with low product behavior risk; do before heavier cost/package changes so image drift is controlled. | Image build uses `npm ci`; pinned digest/SHA checks pass; CLI versions come from repo lockfiles. |
| 4 | SPEC-17a | Adaptive panel experiment and metrics | Cost work should be measured before default changes and kept reversible behind config. | Escalation unit tests plus SPEC-12 corpus comparison for LLM-run count and blocker recall. |
| 5 | SPEC-17b | Wire-or-cut budget, Jira, and reserved config | Clean config truthfulness after adaptive settings are added so docs and schema remain honest. | Tests prove wired features execute, or dead config/code/tests are removed and docs updated. |
| 6 | SPEC-18a | Reducer package boundary | Extract the deterministic core only after platform edges are outside the reducer and config truth is settled. | Reducer test suite runs in isolation with SCM/HTTP imports blocked. |
| 7 | SPEC-18b | Versioned schema spec and package publication | Publish interoperability docs/package metadata last, after the boundary and names have stabilized. | Conformance example validates against published schemas; CI consumes the reducer through the package boundary. |

Split SPEC-15 and SPEC-18 deliberately. The first PR for each should be a seam or
packaging-boundary refactor with no new external behavior; the second PR should
add the new adapter or publication surface. This keeps rollback small if GitHub
state persistence or package naming needs revision.

## Workstream details

### Step 0 — Confirm Phase 2 prerequisites

1. Verify SPEC-12 integration, contract, and security tests exist and run without
   a real GitLab instance.
2. Verify SPEC-13's domain TypedDicts cover anchors/positions, findings, groups,
   persisted state, post results, and gate results.
3. Verify SPEC-14 decomposed `post_consensus` enough that platform I/O can be
   passed in rather than imported directly.
4. Run the standard local gate and record the exact commands in the first Phase 3
   PR body.

### Step 1 — SPEC-15a: platform port and GitLab adapter

1. Add `ai_review/platform/` with a `ReviewPlatform` `Protocol` and small
   platform-neutral types such as `Anchor`, `Position`, `InlineComment`,
   `Thread`, and `ReviewStateNote` that wrap or reuse the SPEC-13 shapes.
2. Move GitLab-specific client access, `build_position`, `gitlab_line_code`,
   discussion marker parsing, hidden-note state, and member/current-user calls
   into `platform/gitlab.py`.
3. Change `post.py`, `input_bundle.py`, and any gate/state code to accept or
   construct a `ReviewPlatform` instead of importing GitLab helpers directly.
4. Keep GitLab as the default adapter and preserve current marker formats and
   persisted state schema unless a schema version bump is explicitly needed.
5. Add contract tests that the GitLab fake satisfies the platform protocol and
   rerun the SPEC-12 golden snapshot to prove behavior did not change.
6. Use `rg` in review notes to demonstrate product code no longer references
   GitLab-specific symbols outside the GitLab adapter, tests, and docs.

### Step 2 — SPEC-15b: GitHub adapter and integration guidance

Before or during this step, tighten the remaining platform response aliases
(`Position`, `InlineComment`, `Thread`, and `ReviewStateNote`) from
`dict[str, Any]` into concrete `TypedDict` shapes once the GitHub response
translation requirements are known.

1. Implement `platform/github.py` against GitHub pull-request review APIs using
   neutral positions translated to `path`, `line`, `side`, and optional
   `start_line` / `start_side` fields.
2. Choose the GitHub state backend before coding: prefer a bot-authored,
   marker-protected PR comment for initial parity unless maintainers approve a
   git-note or check-run backend.
3. Author-verify the state backend using the authenticated bot identity, matching
   SPEC-07's forgery defense rather than trusting marker text alone.
4. Add `posting.mode: github_reviews` and adapter-specific configuration with the
   same validation/effective-summary pattern used for earlier config knobs.
5. Provide a GitHub Actions template/runbook that avoids unsafe
   `pull_request_target` patterns and documents when secrets are available.
6. Add a fake-GitHub E2E mirroring the GitLab SPEC-12 harness: consensus input,
   inline review creation/update, summary upsert, stale-thread handling where
   supported, and gate result.

### Step 3 — SPEC-16: reproducible image and workflow inputs

1. Add an in-repo npm manifest and lockfile for reviewer CLI packages; install
   with `npm ci` and remove mutable repo-variable version sources.
2. Pin Python package inputs through constraints, preferably with hashes where
   the image build process can support them without excessive churn.
3. Pin `python:3.12-slim-bookworm` to a digest and document how to refresh the
   digest safely.
4. Pin GitHub Actions in image-publish workflows to full commit SHAs with comments
   naming the human-readable upstream release tag.
5. Add a drift check that fails when Dockerfiles/workflows reference mutable CLI
   or action versions outside the approved lock/constraints files.
6. Document residual apt reproducibility limits if package-version pinning is not
   feasible in the base image.

### Step 4 — SPEC-17a: adaptive panel and measurement

1. Add config for panel strategy, starting with `full` as the default and an
   opt-in `adaptive` strategy.
2. Implement the cheap first pass as either one reviewer or low-effort reviewers,
   reusing the existing closed-set `effort` validation pattern.
3. Define deterministic escalation triggers for candidate blockers, security,
   correctness, high confidence, schema failures, or ambiguous first-pass output.
4. Preserve the existing full-panel consensus path after escalation so merge
   policy remains unchanged.
5. Add unit tests for every escalation trigger and no-escalation case.
6. Run the SPEC-12 corpus before/after and report average reviewer runs,
   convergence, blocker recall, and false-negative analysis before considering a
   default flip.

### Step 5 — SPEC-17b: budget, Jira, and reserved config truthfulness

1. Inventory every config key and code path flagged as inert by SPEC-02 and the
   Phase 3 spec.
2. For budget, decide one path per PR: implement real persisted per-job/per-MR or
   daily USD enforcement, or remove `budget.py`, budget config, and unreachable
   `budget_skipped` behavior.
3. For Jira, decide one path per PR: wire idempotent comment upsert with stored
   `jira_comment_id` and counters, or remove `jira.*` config/client claims from
   the shipped surface.
4. Move future-looking knobs into a clearly labeled `reserved:` or
   `_experimental:` schema section, or delete them if there is no near-term owner.
5. Update documentation and examples so the default config only advertises
   implemented behavior.
6. Add tests proving each kept knob is read by production code, and remove tests
   for deleted features rather than keeping dead compatibility assertions.

### Step 6 — SPEC-18a: reducer package boundary

1. Define the reducer package contents around canonicalization, anchors,
   consensus, rendering helpers needed by consensus, schema validation, and the
   JSON schemas.
2. Move or wrap modules until reducer imports have no dependency on platform
   adapters, GitLab/GitHub clients, `requests`, CI environment parsing, or posting
   side effects.
3. Add packaging metadata for a monorepo subpackage such as
   `code-tribunal-consensus` with an explicit semver.
4. Create an isolated reducer test target that imports with SCM/HTTP modules
   blocked and runs canonical/grouping/consensus/render/schema tests only.
5. Make the CI engine consume the reducer through the package boundary, even if
   the first landing still uses an editable monorepo dependency.

### Step 7 — SPEC-18b: published schema spec and conformance examples

1. Version the finding, critique, and consensus schemas and document compatibility
   rules for additive fields, required fields, and breaking changes.
2. Write a short schema rationale covering anchors, context hashes, confidence,
   reviewer identity, and the recompute-don't-trust consensus rule.
3. Add a standalone guide showing how another tool can emit finding batches and
   run the reducer without GitLab, GitHub, or CI setup.
4. Add conformance fixtures that validate a minimal and realistic multi-reviewer
   example against the published schemas.
5. Publish package/docs only after maintainers approve the package name, schema
   version, and release workflow.

## Cross-cutting test plan

Run these checks before every Phase 3 PR is marked ready:

```bash
make lint
make test
pytest tests/integration tests/contract tests/security
```

For platform-adapter PRs, also run the GitLab and GitHub fake-client E2E suites
and the golden consensus snapshot. For supply-chain PRs, run the image build or a
Dockerfile/workflow validation substitute in CI. For adaptive-panel PRs, attach
the SPEC-12 corpus report with reviewer-run counts and blocker-recall results.
For reducer-packaging PRs, run the isolated reducer test target with SCM/HTTP
imports blocked.

## Rollout and rollback

- Keep GitLab as the default adapter until GitHub has multiple successful dry
  runs and equivalent fake-client coverage.
- Land the `ReviewPlatform` interface separately from GitHub behavior so a GitHub
  rollback does not unwind the GitLab refactor.
- Treat GitHub state-backend selection as a design checkpoint; changing it after
  users adopt the adapter may require migration tooling.
- Keep adaptive mode opt-in until the corpus shows no blocker-recall regression;
  rollback is a config flip to `panel.strategy: full`.
- Prefer removing stub features over half-wiring them. If budget or Jira is
  removed, call it out as a compatibility change in release notes.
- Keep reducer extraction internal until package names, semver, and schema
  compatibility policy are approved.

## Open decisions

- GitHub state backend: bot-authored PR comment, git note, check-run output, or a
  pluggable backend with one supported default.
- Whether GitHub stale-thread resolution should be best-effort initially if review
  thread mutation APIs cannot exactly match GitLab behavior.
- Exact adaptive first-pass strategy: one reviewer, all reviewers at low effort,
  or a cheap deterministic prefilter before any LLM call.
- Budget direction: real enforcement store versus feature removal.
- Jira direction: real idempotent integration versus feature removal.
- Package and schema names for SPEC-18, including whether the external schema
  should be branded as Code Tribunal or positioned as a neutral LLM-review
  interchange format.
