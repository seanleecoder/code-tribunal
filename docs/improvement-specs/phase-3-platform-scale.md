# Phase 3 — Platform + Scale (Month 3)

> Status: in progress. SPEC-15 (platform adapters) and SPEC-16 (supply-chain
> pinning) are implemented on `main`; SPEC-17 and SPEC-18 remain planned.

Unlocks the largest market (GitHub), makes builds reproducible, cuts cost via an
adaptive panel, and extracts the defensible IP. Assumes Phases 0–2 landed —
especially the platform seam that SPEC-14's decomposition and SPEC-13's types
create.

---

## SPEC-15 — `ReviewPlatform` interface + GitHub adapter

**Implementation status:** complete on `main`.

- **Severity:** Medium (M3) · **Effort:** L · **ROI rank:** 15
- **Depends on:** SPEC-13, SPEC-14 (need typed shapes + a clean `post` seam)

### Why
There is no platform abstraction: discussion/note/version/member APIs,
`build_position`, `gitlab_line_code`, the marker formats, and hidden-note state
are called directly from `post.py`/`input_bundle.py`. GitHub is the majority
market, and today supporting it is a multi-week rewrite, not a plugin. The whole
value proposition (a reproducible multi-model gate) is platform-agnostic; only
the I/O edge is GitLab-shaped.

### Scope
- **In:** a new `ai-review/src/ai_review/platform/` package: an abstract
  `ReviewPlatform` protocol, `gitlab.py` (extract current `GitLabClient` +
  position/marker logic behind it), and a new `github.py` adapter.
- **Out:** achieving 100% GitHub feature parity in one PR (state persistence can
  land in a follow-up); keep GitLab the default.

### Implementation
1. **Define the port** (`ReviewPlatform` Protocol / ABC) covering exactly what
   the pipeline needs: `fetch_diff`, `fetch_version/head_sha`, `list_threads`,
   `create_inline_comment`, `update_comment`, `resolve_thread`,
   `upsert_summary`, `list_notes`/`create_note`/`update_note` (state backend),
   `member_access_level`, `current_user`. Use the SPEC-13 TypedDicts for the
   anchor/position types; introduce a platform-neutral `Position`/`Anchor` and
   have each adapter translate to its native shape
   (GitLab `line_code`/`position`, GitHub `line`/`side`/`start_line`).
2. **Refactor GitLab behind the port**: move `gitlab_client.py`,
   `build_position`, `gitlab_line_code`, marker regexes into
   `platform/gitlab.py` implementing `ReviewPlatform`. `post.py` talks only to
   the interface. (This is why SPEC-14 comes first — the seam must be clean.)
3. **GitHub adapter (`platform/github.py`)**: implement inline review comments
   via the PR review API (path + line + side + `start_line`), summary comment
   with an idempotent marker, thread resolution via review-thread APIs, and a
   **state backend** — GitHub has no hidden MR notes, so store state either in a
   dedicated bot comment (HTML-comment marker, same forgery defense as SPEC-07:
   author-verify the bot) or a git note / check-run output. Document the chosen
   backend behind `state.backend`.
4. **Config/CI**: add `posting.mode: github_reviews`; provide a GitHub Actions
   integration analog to the GitLab CI template — and apply SPEC-06's trust
   lesson (secret-bearing jobs must not run from attacker-controlled workflow
   YAML on `pull_request_target`; document the safe pattern).

### Acceptance criteria
- `post.py`/`input_bundle.py` reference no GitLab-specific symbol directly (only
  the `ReviewPlatform` interface).
- The GitLab path is behavior-identical (SPEC-12 E2E + golden unchanged).
- A GitHub E2E test (fake GitHub client) drives consensus→post→gate and creates a
  PR review with inline comments.

### Tests
- Mirror SPEC-12's fake-client E2E for GitHub. Contract tests that both adapters
  satisfy the `ReviewPlatform` protocol.

### Risk / rollback
- High effort; medium risk. Land the interface + GitLab-behind-it first (no
  behavior change), then GitHub as an additive adapter. The state-backend choice
  for GitHub is the trickiest part — get sign-off before implementing.

---

## SPEC-16 — Supply-chain pinning

**Implementation status:** complete on `main`.

- **Severity:** Medium (M8) · **Effort:** M · **ROI rank:** 17
- **Depends on:** SPEC-03

### Why
Builds are not reproducible and float on mutable inputs: no npm lockfile /
`npm ci` / integrity hashes (only top-level `pkg@version` from **mutable** GitHub
repo variables); `python:3.12-slim-bookworm` is a mutable tag; pip uses `>=`
floors; apt is unpinned; GitHub Actions are pinned to mutable major tags
(`@v6`/`@v4`). (Consumer image digests and OIDC attestation are already good —
keep them.)

### Scope
- **In:** `ai-review/images/base.Dockerfile`, `reviewer.Dockerfile`,
  `.github/workflows/publish-ai-review-images.yml`, a committed npm lockfile and
  pinned Python constraints; move CLI versions from repo variables into the repo.
- **Out:** changing what the images contain.

### Implementation
1. **npm**: add a committed `package.json` + lockfile for the three reviewer
   CLIs and install with `npm ci` (integrity-checked) instead of
   `npm install -g pkg@version`. Pin exact versions in-repo (reviewed via PR),
   not in mutable `vars.*`.
2. **Base image**: pin `python:3.12-slim-bookworm` by `@sha256:` digest; replace
   pip `>=` floors with a pinned constraints file (hashes if feasible); pin apt
   packages or accept and document the residual (apt reproducibility is hard —
   at least record versions).
3. **GitHub Actions**: pin `actions/checkout`, `upload-artifact`, `attest`, etc.
   to full commit SHAs (with a comment naming the version). Consider Dependabot
   for controlled bumps.
4. Keep OIDC + build-provenance attestation and the digest-pinned consumer
   template.

### Acceptance criteria
- Image builds use `npm ci` against a committed lockfile; CLI versions are in the
  repo, not repo variables.
- Base image and all Actions are pinned by digest/SHA.
- A rebuild from the same commit produces the same CLI versions.

### Tests
- CI builds the image on PR (already happens); add a check that fails if a
  version drifts from the lockfile.

### Risk / rollback
- Low-medium. Lockfile maintenance adds a small ongoing cost (mitigated by
  Dependabot). Rollback is reverting the Dockerfile/workflow.

---

## SPEC-17 — Adaptive panel + wire-or-cut budget/Jira + label reserved config

**Implementation status:** planned.

- **Severity:** Medium (M10) + cost/latency (vision) · **Effort:** M · **ROI rank:** 20
- **Depends on:** SPEC-12 (measure cost/quality impact), SPEC-02 (docs)

### Why
Running up to six multi-turn agentic LLM runs per MR (3 reviews + critique) is
expensive and slow, and — per SPEC-11 — the panel often fails to converge, so
you pay 3–6× for a consensus that frequently doesn't form. Separately, a large
fraction of the config surface is dead/reserved (misleading), and budget/Jira are
shipped as "features" but are a stub / unwired.

### Scope
- **In:** the reviewer fan-out / CI orchestration (adaptive panel), `budget.py`
  (finish or remove), `jira_client.py`+`post.py` (wire or remove), `config.py` +
  `review.yaml` (label/remove reserved knobs).
- **Out:** the consensus decision policy (unchanged).

### Implementation
1. **Adaptive panel (cost):** make panel size demand-driven. Design + implement a
   cheap first pass (one reviewer, or all three at low `effort` — reuse the PR#3
   `effort` knob) and escalate to the full panel + critique only when the first
   pass surfaces candidate blockers / security / correctness findings. Gate
   behind config so the always-on panel remains available. Measure cost and
   convergence via SPEC-12 harness + the SPEC-11 metric before/after.
2. **Budget — decide:** either implement a real backend (per-job/per-MR/daily USD
   tracking with an actual store and enforcement) **or** remove `budget.py` and
   the budget config, and delete the unreachable `budget_skipped` path. No middle
   "stub sold as a feature."
3. **Jira — decide:** either wire `jira_client` into `post.py` (idempotent
   comment upsert, set `jira_comment_id`, increment counters) **or** remove it and
   the `jira.*` config and mark it explicitly "not implemented." 
4. **Reserved config:** for every knob the review flagged inert (see SPEC-02's
   table), either wire it or move it to a clearly delimited `reserved:` /
   `_experimental:` section with a schema note, so config no longer presents dead
   knobs as behavior. The `degraded_behavior`/`majority_noise` blocks hint at an
   intended policy-as-config design — either build it or drop it.

### Acceptance criteria
- Adaptive mode reduces average LLM runs per MR on a representative corpus with no
  loss of blocker recall (measured), and is config-gated.
- No stub feature is presented as delivered; every remaining config knob is
  actually read (or explicitly reserved).

### Tests
- Cost/convergence measured via SPEC-12 harness. Unit tests for the escalation
  decision. If budget/Jira are wired, add their tests; if removed, remove dead
  tests.

### Risk / rollback
- Medium. Adaptive panel could lower recall if the first pass misses a blocker;
  keep the full panel as the default until the corpus proves the adaptive mode.
  Removing features is a doc/compat event — announce in CHANGELOG.

---

## SPEC-18 — Extract the deterministic reducer as a standalone package + publish the finding schema

**Implementation status:** planned.

- **Severity:** Strategic (vision: hidden opportunity) · **Effort:** L · **ROI rank:** —
- **Depends on:** SPEC-09, SPEC-13, SPEC-14 (needs a clean, typed, decoupled core)

### Why
The genuinely defensible IP is the reducer: `canonical + anchors + consensus +
schemas` — a deterministic, auditable engine that turns disagreeing LLM outputs
into a reproducible verdict. Packaged as a standalone library + a published
"finding" schema ("SARIF for LLM review"), it's a platform play useful well
beyond code review (any multi-agent verdict system) and a moat competitors can't
easily copy.

### Scope
- **In:** a package boundary around `canonical`, `anchors`, `consensus`,
  `render`, `schema`, and `schemas/*.json`; a versioned, documented finding/
  critique/consensus schema spec; packaging metadata.
- **Out:** the platform/CI/posting layers (they *consume* the reducer).

### Implementation
1. **Draw the seam:** ensure the reducer package has zero dependency on
   `gitlab_client`/`platform`/`requests` (SPEC-09 already removes the last
   coupling). The reducer's inputs are schema-valid finding/critique batches +
   manifest; output is `consensus.json`.
2. **Package it:** publish `code-tribunal-consensus` (or similar) to PyPI with
   its own semver, so the CI engine depends on a versioned reducer. Keep the
   monorepo but define the package boundary in `pyproject`.
3. **Publish the schema as a spec:** promote `finding_batch.schema.json` (+
   critique/consensus) to a documented, versioned **interoperability standard**
   with a short rationale doc (anchors, context hashes, confidence, the
   recompute-don't-trust rule). Invite other tools to emit it. This is the
   ecosystem/standardization bet.
4. **Reference implementation docs:** a "use the reducer standalone" guide (feed
   it N model outputs, get a reproducible verdict) decoupled from GitLab.

### Acceptance criteria
- The reducer installs and runs with **no** SCM/HTTP dependency.
- A published, versioned schema spec exists with a conformance example.
- The CI engine consumes the reducer as a versioned dependency.

### Tests
- Reducer package has its own test suite (largely the existing consensus/
  grouping/canonical tests) runnable in isolation.

### Risk / rollback
- Strategic, not urgent — do it only after the core is clean (Phases 0–2).
  Over-abstracting too early would add packaging overhead without payoff; the
  trigger is "the core is stable and someone else wants to emit our schema."
