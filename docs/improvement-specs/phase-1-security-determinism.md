# Phase 1 — Security + Determinism (Month 1)

Closes the structural security gaps the review flagged as the existential risks
for a *security* tool, plus the one architectural leak that undermines the
reproducible-gate promise. Every spec here assumes SPEC-03 (CI gate) is live.

---

## SPEC-06 — Trusted CI pipeline delivery (fix the MR-controlled definition)

- **Severity:** Critical (C1) · **Effort:** L · **ROI rank:** 2
- **Depends on:** none (but coordinate docs with SPEC-02)

### Why
Adoption is `include: - local: 'ai-review/ci/review.gitlab-ci.yml'`. On
`merge_request_event` pipelines GitLab reads the root `.gitlab-ci.yml` **and its
local includes from the MR source branch**, so a malicious MR author controls the
*job definitions* — not just the reviewed code. They can add a job that
exfiltrates `OPENROUTER_API_KEY`/`GITLAB_WRITE_TOKEN`/`GITLAB_READ_TOKEN`, or
simply write a passing `gate_result.json`. The baked-image adapter boundary
protects the wrong layer. This defeats every downstream control.

### Scope
- **In:** a new integration model and its docs; a reference "trusted parent
  pipeline"; hard requirements on CI/CD variable protection; changes to the
  integration guide in `README.md`. Optionally a validator that detects the
  unsafe pattern.
- **Out:** GitLab-server configuration the project can't ship (document it).

### Implementation
Deliver the secret-bearing (`review`, `critique`, `prepare`) and gate/post jobs
from a location the MR branch **cannot edit**. Pick and document one of:

1. **`include: project` + pinned `ref` (recommended default).** Host the CI
   template in a **separate, protected** repository/branch and have consumers
   `include: { project: 'org/code-tribunal-ci', ref: '<tag-or-protected-branch>',
   file: 'review.gitlab-ci.yml' }`. Because `include: project` resolves against
   the *named ref*, not the MR branch, an MR author cannot alter the job
   definitions. Provide a `codeowners`/protected-branch checklist.
2. **Parent/child pipeline with a trusted child.** The consumer's root pipeline
   is minimal and triggers a child pipeline whose config comes from the pinned
   trusted ref; secrets live only in the child.
3. **Protected trigger token on a protected ref** for the secret jobs.

In all variants:
- **Mandate Protected CI/CD variables.** Update the README variable table
  (currently only "Masked") to require `OPENROUTER_API_KEY`, `GITLAB_READ_TOKEN`,
  `GITLAB_WRITE_TOKEN` be **Protected**, and explain that Protected variables are
  withheld from unprotected MR-branch pipelines — which is the entire point.
- **Kill the `local:` instructions.** Replace the `include: local:` snippet in
  `README.md` with the trusted-ref pattern and a bold warning that `local:` is
  insecure for secret-bearing jobs.
- Optional hardening: a small `scripts/verify_pipeline_trust.py` that a
  maintainer can run to flag a consumer using `local:` for secret jobs.

### Acceptance criteria
- Documented, reproducible integration where a hostile MR that edits the pipeline
  YAML **cannot** obtain Protected variables or forge a passing gate. Validate on
  a scratch GitLab project and record the evidence requested in
  [SPEC-06 Trusted CI Delivery Runbook](spec-06-trusted-ci-runbook.md).
- README no longer instructs `include: local:` for secret-bearing stages.

### Tests
- Manual/scripted validation on a scratch GitLab project (documented runbook in
  the spec's PR). Add `verify_pipeline_trust.py` unit tests if implemented.

### Risk / rollback
- High blast radius on *integration UX* (consumers must restructure). Mitigate
  with a migration guide and keeping the old template working for
  non-secret/advisory-only mode. This is the single most important change; do it
  carefully and with the owner's sign-off on the chosen variant.

---

## SPEC-07 — State-note author verification / HMAC

- **Severity:** High (H1) · **Effort:** S · **ROI rank:** 3
- **Depends on:** none

### Why
`state_note_candidates` selects notes purely by the `ai-review-state:v1` marker;
`decode_state_note_body` verifies a **keyless, self-computed** SHA-256 (integrity,
not authenticity). There is **no author check**. Anyone who can comment on the MR
can forge a state note marking findings `resolved`/`wontfix` (→ never posted,
existing threads auto-resolved with the write token) and poison
`prior_decisions.json` fed into reviewer prompts. Loaded in both trusted
`prepare` (`input_bundle.py`) and `post` (`post.py`). The spec itself
(`specs/…:536`) says only bot-authored notes should count; human `/ai-review`
commands already enforce `access_level ≥ 30` — the far more powerful state note
does not.

### Scope
- **In:** `ai-review/src/ai_review/memory.py`
  (`state_note_candidates`, `newest_valid_state_from_notes`),
  `ai-review/src/ai_review/post.py` (`load_persisted_state`,
  `state_from_existing_discussions` recovery path),
  `ai-review/src/ai_review/input_bundle.py` (`prepare_gitlab_bundle` state load),
  `ai-review/src/ai_review/gitlab_client.py` (needs a "current user" / note
  author accessor — `current_user()` already exists).
- **Out:** redesigning the state schema.

### Implementation
Primary defense — **author allow-listing:**
1. Fetch the bot identity once (`client.current_user()` → user id/username).
2. Thread the note `author` through to `state_note_candidates` /
   `newest_valid_state_from_notes` and **discard any candidate whose author id is
   not the bot**. Notes in GitLab carry `author.id`; the note list already
   includes it.
3. Apply the same author filter to the discussion-marker recovery path
   (`recover_from_discussion_markers`) so recovery can't be seeded by a hostile
   comment either.

Defense in depth — **HMAC the payload (optional but recommended):**
4. Replace the bare `sha256(payload)` with `HMAC-SHA256(payload, secret)` where
   the secret is a CI/CD variable available only to the trusted jobs (never the
   reviewer container). Keep backward-compatible read of legacy notes behind a
   config flag during migration, then require HMAC.

### Acceptance criteria
- A forged `ai-review-state:v1` note authored by a non-bot user is **ignored**
  by both `prepare` and `post`; a warning is recorded.
- Legitimate bot-authored state round-trips unchanged.
- (If HMAC) a note with a valid SHA but invalid HMAC is rejected.

### Tests
- New `tests/security/test_state_note_authenticity.py`: forged-author note is
  dropped; bot-authored note is accepted; (HMAC) tampered-payload rejected.
- Extend `test_find_matching_record` / `test_state_hash` for the author-threaded
  API.

### Risk / rollback
- Medium: existing MRs may hold legacy notes without an author filter passing.
  Mitigate with a migration window that reads legacy notes but re-writes them
  bot-authored/HMAC'd, then flips to strict.

---

## SPEC-08 — Cap severity downgrade at one level; default critique flags false

- **Severity:** High (H6) · **Effort:** S · **ROI rank:** 7
- **Depends on:** none

### Why
CONSENSUS.md §5 says downgrade is "capped at one level," but
`consensus.py:418-425` applies `_severity_after_one_level_downgrade` **once per
disputing critic**, so two third-party disputers take `blocker→major→minor`.
Because the gate blocks only on `blocker`, two biased/prompt-injected peers can
un-block a genuine blocker. Shipped `review.yaml` enables
`allow_severity_downgrade`/`allow_advisory_escalation` even though schema defaults
are `false`.

### Scope
- **In:** `ai-review/src/ai_review/consensus.py` (`_apply_critiques` downgrade
  loop), `ai-review/config/review.yaml` (the two critique flags).
- **Out:** the escalation logic's other branches (leave `agree`/`noise` as-is
  except where the flag default changes them).

### Implementation
1. Change the downgrade loop so the **group** drops at most **one** severity rank
   total, regardless of how many critics dispute — apply the single-level cap to
   the *group outcome*, not per critique. Preserve determinism (still iterate
   sorted critiques; compute the final severity as
   `max(current_rank - 1, min_adjusted_rank_requested)` clamped to one step).
2. Add an invariant: critique adjustments may **never** move a finding across the
   `blocker → non-blocker` boundary (a genuine quorum blocker cannot be
   downgraded out of blocking by critique). Encode as an explicit guard.
3. Flip the shipped `review.yaml` defaults for `allow_severity_downgrade` and
   `allow_advisory_escalation` to `false` (match the schema/`validate_config`
   defaults). Document the security rationale inline.
4. Update `CONSENSUS.md` §5 to match the enforced behavior.

### Acceptance criteria
- With 2–3 third-party disputers on a `blocker`, `final_severity` never drops
  more than one rank and `summary.block_merge` stays `true`.
- Shipped config runs with downgrade/escalation off by default.

### Tests
- Extend `test_phase5_consensus.py`: multi-disputer downgrade is capped at one
  level; blocker-boundary guard holds; a golden assertion that
  `block_merge` survives disputes.

### Risk / rollback
- Low. Behavior becomes stricter (safer). If a deployment wants the old
  permissive behavior, it can re-enable the flags explicitly and accept the
  documented risk — but the boundary guard stays.

---

## SPEC-09 — Extract `render.py`; decouple consensus from post

- **Severity:** High (H5) · **Effort:** S · **ROI rank:** 8
- **Depends on:** SPEC-03 · **Blocks:** SPEC-13, SPEC-14

### Why
The "pure" reducer imports the GitLab-facing presentation module
(`consensus.py` imports `render_body` from `post.py`) solely to compute
`body_hash` from Markdown formatting. A cosmetic comment-template change silently
changes every `body_hash` in the *decision* artifact; it only imports because
`gitlab_client` lazy-imports `requests`. This violates the CONSENSUS.md "no I/O
in the reducer" contract and couples reproducibility to unpinned UI code.

### Scope
- **In:** new `ai-review/src/ai_review/render.py`; `consensus.py` and `post.py`
  imports.
- **Out:** changing the rendered Markdown output (byte-for-byte identical result
  required).

### Implementation
1. Move `render_body`, `compute_body_hash`, `source_hash`, `sanitize_model_text`,
   `validate_suggestion` (the presentation+hash helpers) from `post.py` into a
   new `render.py` with **no** dependency on `gitlab_client`.
2. `consensus.py` imports `render_body`/`compute_body_hash` from `render.py`;
   `post.py` re-imports from `render.py` (or keeps thin wrappers for compat).
3. Add a **version constant** `RENDER_BODY_VERSION` in `render.py` and fold it
   into the `body_hash` input, so an intentional template change is an explicit,
   reviewable version bump rather than a silent artifact change.
4. Confirm `consensus` no longer transitively imports `requests`.

### Acceptance criteria
- `import ai_review.consensus` works in an environment **without** `requests`
  installed.
- A golden `consensus.json` (see SPEC-12) is byte-identical before/after the
  refactor for the same inputs.
- No change to the Markdown actually posted (post-path snapshot unchanged).

### Tests
- A test that imports `consensus` with `requests` uninstalled/blocked (monkeypatch
  `sys.modules`).
- Golden-hash test: same finding group → same `body_hash` pre/post refactor.

### Risk / rollback
- Low, mechanical. The version constant prevents future silent drift.

---

## SPEC-10 — Fork-secret gate + stale-record retention + config-validation completeness

- **Severity:** Medium (M9 + M5 + M13) · **Effort:** S · **ROI rank:** 13, 18
- **Depends on:** SPEC-03

### Why
Three cheap correctness/security gaps bundled (all small, all independent of the
big refactors):
- **M9:** `allow_external_fork_secrets` is read nowhere; no
  `CI_MERGE_REQUEST_SOURCE_PROJECT_ID != CI_PROJECT_ID` gate. The documented
  fork-safety control doesn't exist.
- **M5:** `compact_state` prunes only `resolved`/`superseded`; `stale`/
  `stale_unverified` records are kept forever → long-lived MR hits `max_records`
  (200) → `state_overflow` → `gate.py` treats it as `block_merge=True`. A busy MR
  can self-block.
- **M13:** `decision_for_group` dereferences `config["severity_policy"][...]`
  without `.get`, but `validate_config` doesn't validate `severity_policy` → raw
  `KeyError` on a config that "passed."

### Scope
- **In:** `input_bundle.py`/`ci/review.gitlab-ci.yml` (fork gate),
  `memory.py:compact_state` (retention), `config.py:validate_config`.
- **Out:** the broader dead-config cleanup (SPEC-17).

### Implementation
1. **Fork gate:** in `prepare` (and/or the CI rules), when
   `CI_MERGE_REQUEST_SOURCE_PROJECT_ID != CI_PROJECT_ID` and
   `security.allow_external_fork_secrets` is false, refuse to run the
   secret-bearing path (fail closed with a clear message). Actually consult the
   config key.
2. **Retention:** in `compact_state`, add a retention rule for `stale` /
   `stale_unverified` (e.g. keep last N by run, matching
   `keep_resolved_runs`/`keep_superseded_runs`) so they can't grow unbounded.
   Add config keys under `state.retention` with sane defaults.
3. **Validation:** extend `validate_config` to validate the `severity_policy`
   subtree that consensus dereferences (`single_reviewer_blocker.categories`,
   `quorum_blocker.block_merge`), raising a clean `ConfigError`.

### Acceptance criteria
- A fork MR with `allow_external_fork_secrets: false` does not run secret jobs.
- A synthetic long-lived MR with many `stale` records stays under `max_records`
  after compaction.
- A config missing `severity_policy` raises `ConfigError`, not `KeyError`.

### Tests
- `test_config_env_overrides.py`/new: missing `severity_policy` → `ConfigError`.
- `memory` test: `compact_state` bounds stale records.
- Fork-gate unit test around the prepare decision.

### Risk / rollback
- Low. Retention defaults should be generous enough not to drop live context;
  make them configurable.
