# SPEC-24 — Single GitLab token; fix self-recognition of state notes and posts

- **Severity:** High (state loss / duplicate posts observed in production) · **Effort:** S · **ROI rank:** 2 (pre-1.0)
- **Depends on:** none.

## Why

The pipeline currently requires two GitLab project access tokens
(`GITLAB_READ_TOKEN` for prepare, `GITLAB_WRITE_TOKEN` for post). On GitLab,
**each project access token is its own bot user**, which breaks the
author-authenticity checks the state system relies on:

- `prepare` loads persisted state with the **read** token and verifies
  state-note authorship against the read-bot's user id
  (`input_bundle.py:_load_platform_state` →
  `memory.newest_valid_state_from_notes(expected_author_id=…)`). State notes
  are authored by the **write** bot, so with two distinct tokens prepare
  rejects the pipeline's own state note on **every** run (empty
  `state_aliases.json`, warnings `ignored state note … from non-bot author`).
- Rotating the write token creates a **new** bot user, after which `post`
  distrusts every note and discussion authored by the old bot user →
  duplicate threads and lost dispositions. This matches the maintainer's
  production incident ("posting step didn't recognise its own previous
  posts").

One `api`-scope token is sufficient for both stages; the read/write split
buys no real privilege separation (both jobs run trusted image code in the
same pipeline) and actively causes the identity split.

## Scope

**In:** token resolution in the platform composition root; a summary
diagnostic for the author-mismatch class; docs (README variables table,
TROUBLESHOOTING, ARCHITECTURE, SECURITY/CONTRIBUTING mentions).

**Out:** removing the split-token path (deprecate now, remove post-1.0);
GitHub token handling (unchanged); any weakening of author verification
(rotation still invalidates old notes **by design** — document it).

## Implementation

1. `ai-review/src/ai_review/platform/runtime.py`:
   - Accept `GITLAB_TOKEN` as the preferred variable for both `access="read"`
     and `access="write"`. Fallback order: `GITLAB_TOKEN` → (read:
     `GITLAB_READ_TOKEN`; write: `GITLAB_WRITE_TOKEN`).
   - Emit a one-line stderr deprecation note when a split token is used.
   - Error message when nothing is set names `GITLAB_TOKEN` first.
2. Diagnosability (`ai-review/src/ai_review/memory.py` + callers): when
   `state_note_candidates` rejected ≥1 marker-bearing note for author
   mismatch AND no marker-bearing note survives the author check, append one
   summary warning:
   `all state notes were rejected for author mismatch — this usually means
   the GitLab token was rotated or read/write tokens belong to different bot
   users; see TROUBLESHOOTING`. Surface it in prepare stdout and in
   `post_result.warnings`.
3. Docs:
   - `README.md` CI variables table: one `GITLAB_TOKEN` row (`api` scope,
     Masked + Protected); compatibility note for the legacy names.
   - `docs/TROUBLESHOOTING.md`: new entry "Bot doesn't recognise its own
     previous posts / duplicates after a token change" — project-access-token
     bot identities, rotation consequences (new bot user ⇒ old notes are
     distrusted by design ⇒ one-time re-post), and the two-token identity
     split as a legacy pitfall.
   - `docs/ARCHITECTURE.md` credential table; sweep
     `CONTRIBUTING.md`/`SECURITY.md`/CI template comments for token mentions.

## Acceptance criteria

- A pipeline configured with only `GITLAB_TOKEN` completes prepare→gate with
  state persisted and recognized across two consecutive runs on one MR
  (second run: no `ignored state note` warnings; `skipped_unchanged` > 0).
- Split tokens still work, with a deprecation note in the job log.
- The author-mismatch summary warning appears when all marker-bearing state
  notes are rejected by the author check. If an author-valid note survives
  that check but later fails checksum or payload validation, only the corrupt
  note warning appears because bot identity was verified successfully.

## Tests

- `test_platform_runtime.py`: `GITLAB_TOKEN` used for both accesses;
  precedence over split tokens; split fallback still works and emits its
  deprecation note; missing-token error names `GITLAB_TOKEN`.
- Memory/post unit tests: summary warning emitted exactly when ≥1 author
  rejection and zero marker-bearing notes survive the author check; not
  emitted when an author-valid note survives that check, whether it later
  validates successfully or is reported as corrupt.

## Risk / rollback

Additive token resolution — existing deployments keep working unchanged.
Rollback = revert; no data or schema impact.
