# SPEC-26 — Remove fixed truncation for evidence, dissent, and advisory findings; enforce platform limits instead

- **Severity:** Medium (useful content destroyed by arbitrary caps) · **Effort:** S · **ROI rank:** 4 (pre-1.0)
- **Depends on:** SPEC-25 (shares the `render-body.v2` bump; the dissent and
  evidence sections this spec un-truncates are introduced there).

## Why

Posted output is chopped by arbitrary per-field caps: evidence 300 chars,
body/suggestion 1200 (`render.py:render_body` / `sanitize_model_text`),
advisory (FYI) and fallback findings compressed to one-liners of 160/240
chars in the summary comment (`post.py:_summary_line` / `_one_line`).
Maintainer decision: **do not truncate evidence or advisory findings**; the
safety boundary should be the real platform comment-size limits, not
per-field chopping.

## Scope

**In:** render caps, summary-comment layout, a platform-limit safeguard,
tests. **Out:** `limits.max_fyi_findings` (a count cap, not truncation —
default 50 unchanged); prompt-injection sanitization (redaction +
HTML-comment-marker escaping stays exactly as is).

## Implementation

1. `ai-review/src/ai_review/render.py`:
   - `sanitize_model_text`: keep redaction, marker escaping (`<!--`/`-->`),
     and newline normalization; make `max_length` optional (`None` = no cap).
   - `render_body`: no fixed cap on body, evidence, dissent rationales, or
     suggestion. Keep a generous title cap (240) so headers stay sane.
2. `ai-review/src/ai_review/post.py` summary comment:
   - `_summary_line`: keep the one-line header (severity / category /
     location / one-line-collapsed title) but render the **full body** under
     it as an indented blockquote instead of the 240-char `_one_line` detail.
     Remove the 160/240 hard caps.
3. Platform-limit safeguard (new helper in `render.py`, used by `post.py`):
   - Per-platform max comment size: GitLab notes 1,000,000 characters;
     GitHub comments 65,536 characters. Select by `posting.mode`.
   - Inline bodies: if the rendered body exceeds the platform max, truncate
     at a safe boundary and append
     `…[truncated: platform comment size limit]` **before** the
     `ai-review:v1` marker so the marker always survives intact. The
     truncation must happen **before** `body_hash` is computed on the final
     posted body, so upsert idempotency holds (identical runs → identical
     hash → `skipped_unchanged`).
   - Summary comment: same guard; when over the limit, drop trailing FYI
     entries whole and append `…and N more advisory findings (size limit)` —
     never mid-entry truncation.
4. `RENDER_BODY_VERSION`: single bump to `render-body.v2` shared with
   SPEC-25 (one thread-update wave, one CHANGELOG migration note).

## Acceptance criteria

- No 160/240/300/1200-char truncation anywhere in posted output.
- A pathologically large body degrades only at the platform bound, with an
  explicit truncation marker, an intact `ai-review:v1` marker, and a stable
  `body_hash` across identical runs.
- Summary comments show full advisory bodies; the same content that posts on
  GitLab respects the 65,536-char bound on GitHub.

## Tests

- Render: long evidence/dissent/suggestion survive intact below the cap;
  oversized body triggers the limit marker with marker preservation and
  idempotent hashing (two renders → equal bytes).
- Post: summary comment full-body rendering; oversized summary drops whole
  trailing entries with the count line; GitHub vs GitLab limits selected by
  posting mode.
- Regression: sanitization still strips/escapes HTML comment markers inside
  model text of any length.

## Risk / rollback

Bigger comments on busy MRs — bounded by the platform limits and the
existing count caps (`max_posted_surface_findings`, `max_fyi_findings`).
Rollback = revert (one more thread-update wave from the hash change).
