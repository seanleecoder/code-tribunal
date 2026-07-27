# Phase 3 — Platform + Supply Chain

> Status: complete on `main`; retained as implementation history. The former
> platform composition-root follow-up is recorded as resolved in the
> [completion audit](completion-audit.md).

Adds GitHub support and reproducible image inputs. Paused product ideas are not
part of this phase or the active roadmap.

---

## SPEC-15 — `ReviewPlatform` interface + GitHub adapter

**Implementation status:** complete on `main`; CLI adapter selection now lives
in the platform composition root and is protected by an import-boundary test.

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
