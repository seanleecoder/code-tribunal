# SPEC-23 — GitHub review-thread grouping: working `/ai-review` commands and real thread resolution

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** High (documented feature silently dead on GitHub) · **Effort:** M · **ROI rank:** 1 (pre-1.0)
- **Depends on:** none.

## Why

Human override commands (`/ai-review wontfix|reopen|resolve`) are a documented
feature (`docs/REVISION_LIFECYCLE.md`, `ai-review/README.md`) but are
non-functional on GitHub:

- `post.py:collect_human_commands` requires a command note to be in the same
  thread whose **root note carries the `ai-review:v1` marker**.
- `platform/github.py:_thread_from_comment` maps **every** PR review comment —
  including replies — to its own single-note thread; `in_reply_to_id` is never
  read. A human reply `/ai-review wontfix` therefore lands in a markerless
  thread and is silently ignored.
- The GitHub permission mapping in `member_access_level` (github.py:249-266)
  exists solely for this feature and is currently unreachable.

Related honesty gap that the command feature depends on:
`platform/github.py:resolve_thread` (line ~179) is a **no-op** returning a
fabricated response (REST cannot resolve review threads), yet
`post.py:finalize_state` still increments `resolved_discussions`. Without a
real resolve, `/ai-review resolve` and quorum auto-resolution would update
state but leave GitHub threads open while post_result claims otherwise.

The maintainer wants the feature working on GitHub **and more visible in docs**.

## Scope

**In:** GitHub thread grouping in `list_threads`; GraphQL-based thread
resolution; guarded resolve accounting in `finalize_state`; docs visibility;
tests (unit + fake-server integration).

**Out:** GitLab behavior (already correct); any change to command syntax or
permission thresholds; retry/backoff (SPEC-30).

## Implementation

1. `ai-review/src/ai_review/platform/github.py` — `list_threads`:
   - Group review comments into threads: a comment without `in_reply_to_id`
     is a thread root; a comment with `in_reply_to_id=X` is appended to the
     thread rooted at comment id X (GitHub replies always reference the root
     comment id). If a reply's target is absent from the fetched set, fall
     back to treating that reply as its own single-note thread.
   - Thread shape: `{"id": str(root.id), "notes": [root, replies… sorted by
     created_at then id], "resolved": False, "position": root_position}`.
     Reuse the existing note normalization for every note; notes must keep
     `id`, `body`, `created_at`, and `author: {id, username}` —
     `collect_human_commands` sorts on `created_at`/`id` and reads `author`.
   - Issue comments remain single-note threads (unchanged).
2. Verify `post.py:collect_human_commands` needs no change on the new shape
   (marker on root; commands scanned across all notes; the username path of
   `member_access_level` maps GitHub `write|maintain|admin` → 40 ≥ 30).
3. Real thread resolution on GitHub:
   - Implement `resolve_thread` via GraphQL (`POST {api_url}/graphql`, same
     token): page through
     `pullRequest(number:…){reviewThreads(first:100, after:…){nodes{id isResolved comments(first:1){nodes{databaseId}}}}}`,
     map the root comment `databaseId` to the thread node id, then call the
     `resolveReviewThread` / `unresolveReviewThread` mutation.
   - If the thread node cannot be found, raise `GitHubReviewPlatformError`.
   - `post.py:finalize_state`: wrap the `client.resolve_thread(...)` call in
     `try/except ReviewPlatformError` — on failure append a warning to
     `post_result.warnings` and do **not** increment `resolved_discussions`.
     (This also fixes the currently-uncaught GitLab resolve failure path.)
4. Docs visibility (explicit maintainer request):
   - `README.md`: add a "Human override commands" Key Features bullet with the
     three commands, one-line semantics, and platform mechanics (GitLab: reply
     in the finding's discussion; GitHub: reply on the bot's inline review
     comment).
   - `docs/REVISION_LIFECYCLE.md` "Human overrides": document both platforms
     and required permission (GitLab: developer/30+; GitHub:
     write/maintain/admin).
   - `docs/TROUBLESHOOTING.md`: add "My `/ai-review` command was ignored"
     (wrong thread, insufficient permission, command not alone on its own line
     — `COMMAND_RE` is line-anchored).

## Acceptance criteria

- On a GitHub PR, a write-permission user replying `/ai-review wontfix` on a
  bot review comment causes the next run to mark the record `wontfix` and not
  re-raise the finding.
- `/ai-review resolve` (and quorum auto-resolution) actually resolves the
  GitHub review thread; `resolved_discussions` counts only real resolutions;
  resolve failures degrade to a warning, never a crash.
- Docs updated in the three named places.

## Tests

- `test_github_platform.py`: reply grouping (root + 2 replies; interleaved
  threads; orphan-reply fallback); GraphQL resolve happy path and
  missing-thread failure via a fake session.
- `test_post.py`: `collect_human_commands` with GitHub-shaped threads —
  command in reply honored; `triage` permission rejected; markerless thread
  ignored; resolve-failure path leaves `resolved_discussions` unchanged and
  appends a warning.
- `tests/support/fake_github.py`: serve replies with `in_reply_to_id` and a
  minimal GraphQL endpoint; extend the platform contract tests
  (`tests/contract/test_review_platform.py`) so the command scenario runs on
  both platforms.

## Risk / rollback

Grouping only changes the GitHub adapter's thread shaping; state matching
reads root notes, which are unchanged. Rollback = revert the adapter commit;
the command feature returns to its previous (dead) state without data damage.
GraphQL resolution is additive; its failure mode is a warning.
