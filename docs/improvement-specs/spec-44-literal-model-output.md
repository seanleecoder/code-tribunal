# SPEC-44 — Literal-safe rendering of model output

- **Severity:** High (untrusted model output can alter the review that a maintainer sees) · **Effort:** M · **ROI rank:** post-1.0
- **Depends on:** none.

## Rationale

The review pipeline treats model output as data while validating it, but the posting
path still interpolates several dynamic strings into Markdown. A title, body,
evidence item, reviewer name, path, rationale, or suggestion can therefore be
interpreted by GitHub or GitLab as structure rather than displayed as the literal
value the model supplied. This is both misleading and an avoidable injection
surface: `$...$` can become KaTeX-like math, `# ...` can become a heading, `>` and
`-` can escape their intended layout, and an unmatched triple-backtick sequence can
consume the bot-owned footer or marker.

The product must make a clear trust boundary: only renderer-owned labels,
delimiters, lists, emphasis, and machine markers may be Markdown. Every dynamic
value must be literal data.

## Scope

**In:** the common rendering path for inline threads and summary comments; all
visible dynamic values (model text, reviewer names, paths, titles, bodies,
evidence, critique rationale, and suggestions); GitHub and GitLab platform-size
handling; rendering tests and golden fixtures.

**Out:** changing consensus, voting, redaction rules, reviewer prompting, schema
meaning, or allowing a supported subset of model-authored Markdown. This is not a
formatting-preference feature: model-originated content is never partially trusted
Markdown.

## Rendering contract

### One renderer owns every dynamic value

Introduce a small renderer-owned literal API in
[`ai-review/src/ai_review/render.py`](../../ai-review/src/ai_review/render.py), for
example `literal_span(value)` and `literal_block(value)`. Both `render_body` and the
summary-entry renderer in
[`ai-review/src/ai_review/post.py`](../../ai-review/src/ai_review/post.py) must call
that API. They must not call `sanitize_model_text` and then interpolate the returned
string directly into Markdown.

The API has these invariants:

1. It first applies the current redaction, HTML-comment-marker escaping, CRLF/CR to
   LF normalization, and outer-whitespace trimming. The existing 240-character
   title cap remains a renderer policy and is applied before the title span is
   wrapped. No other old per-field cap is restored.
2. `literal_span` is used for scalar values: titles, reviewer names, paths,
   severity/category values, source IDs shown to a maintainer, and other short
   labels. It renders a Markdown code span whose delimiter is one backtick longer
   than the longest contiguous backtick run in the normalized value. A newline in a
   scalar is rendered as the literal two-character sequence `\n`, so no dynamic
   scalar can cross a renderer-owned line or list boundary.
3. `literal_block` is used for multiline values: body, evidence, rationale, and
   suggestion. It emits a `text` fenced block using
   `max(3, longest_backtick_run(value) + 1)` backticks for both delimiters. The
   opening and closing delimiters, surrounding newlines, and field labels are
   renderer-owned. A model-supplied fence can therefore never close the outer fence.
4. Empty optional values are omitted with their entire bot-owned field label. The
   renderer never substitutes a model-derived placeholder.
5. The only HTML comments in a rendered payload are the renderer-owned
   `ai-review:v1` / `ai-review-summary:v1` markers. Marker attributes are emitted by
   a dedicated marker encoder, not by the literal-value API.

The following is illustrative output for a hostile body. The four-backtick wrapper
is chosen by the renderer because the value contains a three-backtick run; the
heading, quote, list, math-looking text, and comment-looking text remain literal.

`````markdown
**AI review**

Title: `PHP template output is literal`

Body:
````text
# not a heading
> # not a quoted heading
- not a list
```php
echo "$total"; // $not_math$
```
<!-- not a bot marker -->
````
`````

The same field must have the same literal treatment in an inline discussion and in
the summary comment. A summary may use renderer-owned headings and entry lists, but
it must not rely on a blockquote or a raw one-line interpolation to make model text
safe.

### Suggestions are data, not executable Markdown

Retire the posting decision that suppresses a suggestion because its
model-supplied triple-backtick count is unbalanced. `validate_suggestion` may be
removed or narrowed to a legacy parsing helper, but it must no longer gate whether a
suggestion is shown. A suggestion is rendered through `literal_block` exactly like a
body; malformed inner fences, HTML-comment-like text, and Markdown directives are
visible as text rather than interpreted as syntax.

### Limits, hashes, and markers

Keep the platform limits from SPEC-26: 1,000,000 characters for GitLab discussions
and 65,536 for GitHub review comments. Apply the limit to the final literal-rendered
payload, before the bot marker and before `body_hash` is calculated.

Replace the current raw backtick-count truncation heuristic with renderer-owned
fragments or an equivalent token stream. If inline truncation ends inside a literal
block, the renderer must append that block's exact owned closing delimiter before
the existing platform-truncation notice, trusted footer, and marker. It must never
infer safety from model text. Summary comments continue to drop whole rendered
entries and append their existing size-limit trailer; they never cut an entry or a
literal fence in half.

`body_hash` and the summary hash are calculated from the final redacted,
normalized, literal-rendered, size-limited body. Equal inputs must therefore produce
identical bytes and hashes on both platforms. Redaction, newline normalization,
marker parsing, source hashes, and idempotent upsert behavior remain unchanged.

## Exact implementation surface

1. In `render.py`, set `RENDER_BODY_VERSION` to `render-body.v3`; add the shared
   literal span/block renderer and make `render_body`, evidence, critique text, and
   suggestions consume it. Keep bot-owned labels, consensus footer, and marker
   grammar outside untrusted fragments.
2. In `post.py`, replace `_summary_line`'s raw dynamic interpolation and quoted
   detail path with the same renderer-owned entry primitives. Route locations,
   reporter names, titles, bodies, and all future optional fields through those
   primitives.
3. Preserve the current platform-limit API, but change its input from an arbitrary
   Markdown string to renderer-owned fragments (or an equivalent representation)
   so a truncation can close only a known renderer fence.
4. Refresh rendering goldens, body-hash fixtures, and marker-parser fixtures. The
   parser must continue to recognize both existing v2-rendered bot markers and v3
   markers because the marker grammar itself does not change. No consensus, finding,
   or state schema version changes are required for this specification.
5. When implementation lands, update the current rendering/reference documentation
   and CHANGELOG in that implementation change. This proposed specification does
   not itself advertise the behavior as shipped.

## Migration and rollback

`render-body.v3` intentionally changes every active inline body hash. On the first
post after upgrade, each matching bot-owned thread receives one content refresh;
its issue ID, state record, source marker, and resolution status are retained. The
summary note updates through its normal hash-based upsert.

There is no artifact migration. Older state records remain usable because their
markers and issue IDs do not change. A rollback restores the prior renderer and
causes one reverse refresh; it must not discard state or attempt to parse historical
model Markdown as trusted structure.

## Acceptance criteria

- No visible dynamic value can create a heading, quote, list, math rendering,
  HTML comment, marker, or code-fence boundary in either supported platform.
- A title containing backticks and a multiline field containing arbitrary backtick
  runs render literally without corrupting the bot footer or marker.
- Redaction and newline normalization still occur before display; a malformed
  suggestion is shown safely rather than silently omitted.
- Inline bodies and summary entries respect their existing platform limits, retain
  the trusted marker, and have stable hashes across identical reruns.
- The only intentional posting churn is the one-time v3 body refresh.

## Required tests

- `ai-review/tests/security/test_prompt_injection_rendering.py` — add
  `test_literal_renderer_neutralizes_math_headings_quotes_lists_and_comment_text`
  with KaTeX-like PHP, `#`/`>`/list tokens, and marker-looking input.
- `ai-review/tests/unit/test_body_hash.py` — add
  `test_render_v3_closes_renderer_owned_fence_before_stable_truncation`; run it for
  GitLab and GitHub limits twice and assert byte-identical body/hash pairs and an
  intact marker. Add a paired `parse_marker` regression for retained v2/v3 marker
  compatibility.
- `ai-review/tests/unit/test_post.py` — add
  `test_summary_uses_literal_renderer_for_reviewer_path_title_evidence_and_suggestion`
  and `test_malformed_suggestion_fence_is_rendered_not_dropped`.
- `ai-review/tests/contract/test_golden_consensus.py` — refresh the inline and
  summary rendering goldens, including an adversarial fence-escape fixture.
- Cross-platform integration coverage in
  `ai-review/tests/integration/test_post_gate_e2e.py` — post the same hostile group
  through `gitlab_discussions` and `github_reviews`, then assert no extra thread,
  no marker loss, and deterministic unchanged-on-rerun behavior.
