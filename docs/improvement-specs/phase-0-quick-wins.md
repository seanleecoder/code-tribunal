# Phase 0 — Quick Wins (Week 1)

> Status: complete. Retained as implementation and decision history.

All specs here are XS/S, independent, and low-risk. **Do SPEC-03 first** so the
rest land behind a real CI gate. None require touching the consensus engine.

---

## SPEC-01 — Add LICENSE + OSS scaffolding

- **Severity:** Critical (C2) + Medium (M14) · **Effort:** XS · **ROI rank:** 1, 19
- **Depends on:** none

### Why
There is no `LICENSE` anywhere, so the code is legally "all rights reserved" —
nobody can use, fork, or contribute. For a security tool, the absence of a
vulnerability-disclosure policy is also a blocker. This is the cheapest,
highest-leverage change in the whole program.

### Scope
- **In:** root `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `.github/ISSUE_TEMPLATE/` (bug + feature), `.github/PULL_REQUEST_TEMPLATE.md`,
  a starter `CHANGELOG.md`, and a `v0.1.0` git tag.
- **Out:** relicensing decisions beyond choosing Apache-2.0; do not modify source
  file headers in this spec (optional follow-up).

### Implementation
1. Add `LICENSE` = Apache-2.0 full text (patent grant matters for a security
   tool). Set copyright holder to the project owner.
2. `SECURITY.md`: private disclosure channel (email or GitHub private advisory),
   scope, response-time expectation, and an explicit list of the *known*
   unresolved issues being tracked (link C1/H1/H2/H3) so reporters don't
   re-file them.
3. `CONTRIBUTING.md`: dev setup (`pip install -e .[dev]`), how to run
   `make test`/`make lint`, the PR checklist (ruff+mypy+pytest green — see
   SPEC-03), and a short "how to add a reviewer backend" pointer.
4. Issue templates: `bug_report.yml` (repro, expected/actual, pipeline stage,
   reviewer, logs), `feature_request.yml`. PR template: summary, finding ID(s)
   addressed, tests, risk.
5. `CHANGELOG.md`: "Keep a Changelog" format; seed with `Unreleased` +
   `0.1.0` from git history.
6. Tag `v0.1.0` on the current `main` head.

### Acceptance criteria
- Repo root shows a recognized Apache-2.0 license (GitHub license detection).
- `SECURITY.md` present with a working disclosure channel.
- `git tag` lists `v0.1.0`.

### Tests
- None (docs). CI (SPEC-03) must still pass.

### Risk / rollback
- None. Purely additive.

---

## SPEC-02 — README accuracy pass

- **Status:** complete; superseded by the 2026-07-14 active-config cleanup.
- **Depends on:** none

The original accuracy pass separated implemented controls from future-facing placeholders. The follow-up cleanup removed all inert configuration and associated product claims, so the shipped config and docs now describe implemented behavior only. Paused product ideas live outside the active improvement specs.

### Acceptance criteria

- Product documentation contains no reserved-config table or inert config example.
- Every top-level key in the shipped config is accepted and used by production code.
- Paused capabilities are absent from runtime schemas and artifacts.

---

## SPEC-03 — Test / lint / type CI on pull requests

- **Severity:** High (H4) · **Effort:** S · **ROI rank:** 4
- **Depends on:** none · **Blocks:** everything else (safety net)

### Why
`ruff` and `mypy strict=true` are configured in `pyproject.toml` but run
**nowhere** in CI; tests execute only as a side effect of Docker image builds
(via `unittest`, no coverage). There is no required quality gate on the
security-critical modules. This is the prerequisite safety net for every
refactor spec.

### Scope
- **In:** new `.github/workflows/ci.yml`.
- **Out:** changing the image-publish workflow; fixing mypy errors that surface
  (see step 4 — may spill into a follow-up).

### Implementation
1. New workflow `ci.yml`, triggers `pull_request` + `push` to `main`,
   `permissions: contents: read`.
2. Job `quality` on `ubuntu-latest`, Python 3.12:
   - `pip install -e '.[dev]'`
   - `ruff check ai-review/src ai-review/tests`
   - `pytest ai-review/tests --cov=ai_review --cov-report=term-missing`
   - `mypy` (see step 4).
3. Cache pip (`actions/setup-python` cache) to keep it fast.
4. **mypy reality check:** run `mypy` locally first. If strict mypy does not
   pass today (likely, given the pervasive `dict[str, Any]`), do **not** block
   the PR on the whole tree. Instead: add `mypy` in a **non-blocking** step
   (`continue-on-error: true`) initially, OR scope strict mypy to the already-
   clean leaf modules (`canonical`, `anchors`, `gate`, `redact`, `trigger`,
   `gate`) via per-module overrides, and record the gap for SPEC-13 to close.
   Make ruff + pytest **blocking** immediately.

### Acceptance criteria
- A PR that breaks a lint rule or a test fails CI.
- Coverage is reported in the job log.
- mypy runs (blocking for the scoped-clean set, or non-blocking tree-wide) with
  the decision documented in the workflow comments.

### Tests
- The workflow is the test. Verify by opening a draft PR with a deliberate ruff
  violation and confirming red.

### Risk / rollback
- Low. If mypy is too noisy, keep it non-blocking; never merge a config that
  makes the gate flaky.

---

## SPEC-04 — Exact-host endpoint pinning for the claude adapter

- **Severity:** High (H3) · **Effort:** XS · **ROI rank:** 6
- **Depends on:** SPEC-03 (for the test to run in CI)

### Why
`claude.sh` maps `OPENROUTER_API_KEY` → `ANTHROPIC_AUTH_TOKEN` whenever
`ANTHROPIC_BASE_URL` matches the **substring** `*openrouter.ai*`. Hosts like
`https://openrouter.ai.attacker.com` or `https://openrouter.ai@attacker.com`
match and receive the real key. codex/opencode are pinned to the exact canonical
host; claude is not. **PR #3 added a second `*openrouter.ai*` glob** (the
`--bare` guard), so there are now two sites to fix.

### Scope
- **In:** `ai-review/adapters/claude.sh` (both glob sites),
  `ai-review/src/ai_review/adapter_runner.py`
  (`_cli_reviewer_validation_error`).
- **Out:** codex/opencode (already exact-pinned).

### Implementation
1. In `adapter_runner._cli_reviewer_validation_error`, extend the endpoint check
   to `claude`: reject unless `ANTHROPIC_BASE_URL` is unset or exactly the
   canonical OpenRouter Anthropic base (`https://openrouter.ai/api`) — mirror the
   existing codex/opencode branch that validates `OPENROUTER_BASE_URL`. Return a
   clean `model_error`-style message so the adapter is never spawned with a bad
   endpoint.
2. In `claude.sh`, replace **both** `case … in *openrouter.ai*)` matches with an
   exact-string comparison against the canonical base (and its `/api` variant as
   used by the CI template). Use `[ "$ANTHROPIC_BASE_URL" = "https://openrouter.ai/api" ]`
   rather than a glob.
3. Keep the empty/unset case behaving as today (native Anthropic route).

### Acceptance criteria
- Setting `ANTHROPIC_BASE_URL=https://openrouter.ai.evil.com/api` causes the
  claude reviewer to fail fast with a clear endpoint error and **never** exports
  the token to that host.
- The legitimate `https://openrouter.ai/api` route still works (mock-level test).

### Tests
- Unit test in `test_openrouter_adapters.py` / `test_adapter_runner.py`:
  `_cli_reviewer_validation_error("claude", model, env={ANTHROPIC_BASE_URL: hostile})`
  returns an error; canonical host returns `None`.
- Shell-level: a `test_*` that asserts `claude.sh` rejects a hostile base (can
  be a lightweight `bats`-style or python `subprocess` test invoking the script
  with a stub `claude` on PATH).

### Risk / rollback
- Low. If a deployment legitimately uses a different Anthropic-compatible host,
  make the canonical host a validated allow-list of exact hosts, not a glob.

---

## SPEC-05 — Redact posted finding bodies + surface silent remap degradation

- **Severity:** Medium (M6 + M12) · **Effort:** XS · **ROI rank:** 14
- **Depends on:** SPEC-03

### Why
- **M6:** `redact_text` is applied to logs but never to the model-authored
  `title`/`body`/`evidence`/`suggestion` that `post.py` writes into GitLab with
  the write token. A prompt-injected reviewer can get a secret posted verbatim.
- **M12:** `_load_current_diff_text` swallows exceptions → `None` → inline remap
  is skipped and comments can post at stale anchors **with no warning**.

### Scope
- **In:** `ai-review/src/ai_review/post.py` (`sanitize_model_text` / `render_body`
  / `_summary_line` path; `_load_current_diff_text` and its caller).
- **Out:** changing the redaction patterns themselves (that's L2, separate).

### Implementation
1. Apply `redact.redact_text` to model-authored strings (`title`, `body`,
   `evidence`, `suggestion`) inside `sanitize_model_text` (or immediately before
   composing the posted body / summary line). Keep the existing marker/fence
   escaping — redaction is *additional*.
2. In `_load_current_diff_text`, when the fetch fails, append a structured
   warning to `result["warnings"]` (e.g. `"diff_fetch_failed: inline remap
   skipped, anchors may be stale"`) so the degradation is visible in
   `post_result.json`, not silent.

### Acceptance criteria
- A finding whose `body` contains a fake `sk-...`/`glpat-...`/JWT is posted with
  `[REDACTED]` in place of the secret.
- A simulated diff-fetch failure produces a warning in `post_result.json` and
  still degrades gracefully to summary/no-remap.

### Tests
- `test_post.py`: posting a finding with an embedded secret asserts redaction in
  the composed body; a stubbed diff-fetch raising asserts the warning is present.

### Risk / rollback
- Low. Redaction could theoretically mangle a legitimate finding that quotes a
  token-shaped string; acceptable trade-off for a security tool, and rare.
