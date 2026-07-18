# SPEC-30 — Post-1.0 robustness follow-ups: posting resilience, diff endpoint, fallback-parser removal

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** Medium (robustness/maintenance; no correctness regression today) · **Effort:** M · **ROI rank:** 8 (may land after 1.0)
- **Depends on:** SPEC-23 (resolve-path error guarding lands there); SPEC-24
  (split-token deprecation completes here).

Three independent robustness items plus one deprecation completion. Safe to
ship in 1.0.x.

## 1. Posting resilience (review F10)

The inline **create** path degrades gracefully
(`post.py:_create_inline_discussion` catches `ReviewPlatformError` → warning
+ summary fallback), but the **update** path
(`_update_existing_inline_discussion` → `client.update_comment`) is uncaught:
a transient 5xx aborts `post_consensus` with a traceback before
`post_result.json` is written, so the gate fails on a missing artifact
instead of a structured `failed_post_result`.

**Fix:**
- Wrap the update call with the same handling as create: on
  `ReviewPlatformError`, append a warning, add the group to the summary
  fallback, continue. (The resolve path is guarded in SPEC-23.)
- Add a small bounded retry (3 attempts, exponential backoff ~1s/2s/4s) for
  idempotent HTTP verbs (GET/PUT/PATCH) in both `gitlab_client.py:_send` and
  `platform/github.py:_request`, retrying on 429/5xx/connection errors only.
  POST (creation) is NOT retried — a timeout after server-side success would
  duplicate threads.

**Tests:** update-failure degrades to summary fallback with `partial_failed`
semantics preserved; retry logic (fake session returning 502,502,200);
POST never retried.

## 2. GitLab diff endpoint (review F11)

`gitlab_client.py:fetch_mr_diff` uses the deprecated
`/merge_requests/:iid/changes` endpoint, unpaginated, and ignores the
response's `overflow`/collapsed-diff signals — a very large MR can be
reviewed against a silently truncated diff, contradicting the loud
`max_diff_bytes` fail-fast philosophy.

**Fix:** migrate to the paginated `/merge_requests/:iid/diffs` endpoint
(reuse `_get_all_pages`); reconstruct the unified diff as today (the
downstream parser needs only `diff --git` + `---`/`+++` headers and hunks);
fail with a `BundleError`-surfaced message when any returned change is
collapsed/truncated (`overflow` semantics) instead of proceeding silently.

**Tests:** fake-GitLab pagination across pages; overflow → loud failure;
byte-compatibility of the reconstructed diff with the current fixtures.

## 3. Fallback parser deletion (review F8)

`config.py` carries a hand-rolled YAML-subset parser (~110 lines,
`_strip_comment`/`_parse_scalar`/`_tokenize_yaml_subset`/`_parse_block`) and
`schema.py` a hand-rolled JSON-Schema-subset validator (~95 lines,
`_validate_subset` + helpers). Both run only when PyYAML/jsonschema are
missing — but both are hard dependencies in `pyproject.toml` **and** pinned
into the runtime image. The subsets have subtly different semantics
(partial `if/then/else`, no `additionalProperties`-pattern support, naive
scalar parsing): a latent validation-divergence hazard with zero production
value.

**Fix:** delete both fallbacks; import PyYAML/jsonschema unconditionally and
let a missing dependency fail fast. Remove the fallback-exercising branches
from tests (keep the semantic tests running against the real libraries).

## 4. Split-token removal (completes SPEC-24)

Remove the deprecated `GITLAB_READ_TOKEN`/`GITLAB_WRITE_TOKEN` fallback from
`platform/runtime.py`, its deprecation notice, and remaining doc mentions.
Requires one released version with the deprecation notice first.

## Acceptance criteria

- A transient API failure during an update run yields a structured
  `post_result.json` (warnings + fallback), never a traceback-only red job.
- Oversized-MR truncation by GitLab fails the prepare stage loudly.
- `config.py`/`schema.py` contain no parser/validator fallbacks; suite green.
- After item 4, only `GITLAB_TOKEN` is accepted and documented.

## Risk / rollback

Retries lengthen worst-case post duration (~bounded +7s per failing call).
The diff-endpoint migration is behavior-compatible for non-truncated MRs and
strictly safer for truncated ones. Each item reverts independently.
