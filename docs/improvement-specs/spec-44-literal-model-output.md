# SPEC-44 — Literal-safe rendering of model output

- **Severity:** High (untrusted model output can alter the review that a maintainer sees) · **Effort:** L · **ROI rank:** post-1.0
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
delimiters, lists, emphasis, disclosure elements, and machine markers may be
Markdown.

**Every free-text or path-shaped value must be literal data.** That is the boundary
this specification enforces, and it is deliberately narrower than "every dynamic
value". Closed enumerations — `severity`, `category`, `decision`, and the other fixed
vocabularies — are constrained by `finding_batch.schema.json` before they reach the
renderer, so a model cannot place arbitrary bytes in them and they remain
renderer-owned text. Stating the limit here is a decision, not an exemption
discovered later: a rule of "every dynamic value" would either be violated by the
existing severity header or would force closed enums through a literal API that buys
no safety.

## Design decision: fenced literals over selective escaping

Two mechanisms can make a dynamic value literal. This specification chooses
fencing, and records the cost so the choice is not revisited by accident.

**Chosen — wrap in renderer-owned code spans/fences.** Safety is structural: the
delimiter is computed from the value, so correctness does not depend on
enumerating Markdown constructs. The cost is real and affects every comment a
maintainer reads: a prose `body` becomes a monospace block with no soft wrap, so
long lines scroll horizontally, and any legitimate model paragraphing or emphasis
is flattened. SPEC-45's disclosure-collapsed sections are the mitigation for the
resulting vertical bulk.

**Rejected — backslash-escape prose, fence only code-shaped fields.** This
preserves proportional text flow and reads better. It is rejected because
correctness becomes a completeness argument over an open-ended construct list
(line-leading `#`, `>`, `-`, `*`, `+`, `N.`, `|`, plus backticks, `$` math,
autolinks, raw HTML, and reference-link syntax), and that list differs between
GitHub and GitLab. A single missed construct is a silent injection. Escaping may
be reconsidered only if it is expressed as a proven-complete transform with
per-platform fixtures, not as a hand-maintained character list.

Readability is therefore a known, accepted regression of this specification, not
an oversight.

## Scope

**In:** the common rendering path for inline threads and summary comments; every
visible free-text or path-shaped value (model text, reviewer names, paths, titles,
bodies, evidence, critique rationale, and suggestions); GitHub and GitLab
platform-size handling; rendering tests and golden fixtures.

**Out:** changing consensus, voting, redaction rules, reviewer prompting, schema
meaning, or allowing a supported subset of model-authored Markdown. This is not a
formatting-preference feature: model-originated free text is never partially trusted
Markdown. Also out: routing schema-validated closed enums through the literal API
(see the boundary statement in the rationale).

## Rendering contract

### One renderer owns every free-text and path-shaped value

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
2. `literal_span` is used for scalar values: titles, reviewer names, paths, source
   IDs shown to a maintainer, and other short free-text or path-shaped labels. It
   renders a Markdown code span whose delimiter is one backtick longer than the
   longest contiguous backtick run in the normalized value. A newline in a scalar is
   rendered as the literal two-character sequence `\n`, so no dynamic scalar can
   cross a renderer-owned line or list boundary.

   Per the boundary statement in the rationale, schema-validated closed enums are
   outside this API. `severity`, `category`, `decision`, and the other fixed
   vocabularies keep their current presentation — in particular the existing
   `**AI review: MAJOR correctness**` header line is retained verbatim.
3. `literal_block` is used for multiline values: body, evidence, rationale, and
   suggestion. It emits a `text` fenced block using
   `max(3, longest_backtick_run(value) + 1)` backticks for both delimiters. The
   opening and closing delimiters, surrounding newlines, and field labels are
   renderer-owned. A model-supplied fence can therefore never close the outer fence.
4. Empty optional values are omitted with their entire bot-owned field label. The
   renderer never substitutes a model-derived placeholder. A **required** scalar that
   normalizes to empty — for example a title that redacts to nothing — renders as the
   renderer-owned literal `(empty)` outside any span, because an empty code span
   displays as stray backticks. This is renderer-owned text, not a model-derived
   placeholder.
5. The only HTML comments in a rendered payload are the renderer-owned
   `ai-review:v1` / `ai-review-summary:v1` markers. Marker attributes are emitted by
   a dedicated marker encoder, not by the literal-value API.
6. Renderer-owned `<details>` / `<summary>` disclosure elements are permitted
   structure. They are never derived from model output, they carry only
   renderer-owned summary text and counts, and they wrap fragments that are already
   literal. Both supported platforms render them; GitHub requires a blank line after
   `</summary>` before a fenced block is recognized, so the renderer must emit that
   blank line unconditionally.

   This invariant exists solely to support SPEC-45's tiered critique display, which is
   the mitigation for the vertical bulk that fencing introduces. It is not
   independently motivated: if that tiering is abandoned, this invariant is removed
   with it and the permitted-structure list reverts to labels, delimiters, lists,
   emphasis, and markers.

The following is illustrative output for a hostile body. The four-backtick wrapper
is chosen by the renderer because the value contains a three-backtick run; the
heading, quote, list, math-looking text, and comment-looking text remain literal.

`````markdown
**AI review: MAJOR correctness**

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
fragments or an equivalent token stream. It must never infer safety from model text.
Truncation obeys two fragment rules:

- A cut may land inside a `literal_block`. The renderer then appends that block's
  exact owned closing delimiter before the existing platform-truncation notice,
  trusted footer, and marker.
- A cut may **never** land inside a `literal_span`. Spans are atomic: truncation
  falls back to the preceding fragment boundary and drops the whole span with its
  bot-owned label. A partially rendered span would leave an unmatched backtick run
  and is never emitted.

Summary comments continue to drop whole rendered entries and append their existing
size-limit trailer; they never cut an entry, a span, or a literal fence in half.

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
4. Restructure `render_summary_body` from its current hand-rolled prefix-length
   arithmetic over exactly two hardcoded sections into a **list of section
   descriptors** — each carrying its header factory, rendered entries, trailer
   factory, and an explicit drop priority — with one generic
   drop-lowest-priority-trailing-entry loop. The composed string remains the source
   of truth and the existing recheck loop is retained.

   This refactor belongs here rather than in a later specification. SPEC-44 already
   has to convert this function's input to fragments; SPEC-45 adds a third section
   and SPEC-46 a fourth, each with its own trailer and a position in a strict global
   retention order. Patching the size arithmetic and the `drop_trailing_entry` tuple
   three times is how layout and size accounting drift apart. After this change,
   adding a section is data.
5. Refresh rendering goldens, body-hash fixtures, and marker-parser fixtures. The
   parser must continue to recognize both existing v2-rendered bot markers and v3
   markers because the marker grammar itself does not change. No consensus, finding,
   or state schema version changes are required for this specification.
6. When implementation lands, update the current rendering/reference documentation
   and CHANGELOG in that implementation change. Record the accepted readability
   regression and the rejected escaping alternative in the rendering reference so the
   tradeoff is discoverable outside this specification. This proposed specification
   does not itself advertise the behavior as shipped.

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

- No visible free-text or path-shaped value can create a heading, quote, list, math
  rendering, HTML comment, marker, or code-fence boundary in either supported
  platform. Closed schema-validated enums are out of scope by the stated boundary,
  and a schema test asserts each such vocabulary is closed so the exemption stays
  earned rather than assumed.
- A title containing backticks and a multiline field containing arbitrary backtick
  runs render literally without corrupting the bot footer or marker.
- Redaction and newline normalization still occur before display; a malformed
  suggestion is shown safely rather than silently omitted.
- Inline bodies and summary entries respect their existing platform limits, retain
  the trusted marker, and have stable hashes across identical reruns.
- Truncation never emits a partially rendered literal span, and never leaves a
  literal block unclosed.
- Schema-validated enums keep their existing renderer-owned presentation; the
  `**AI review: <SEVERITY> <category>**` header is unchanged. Any future field added
  to a rendered payload is literal by default; exempting one requires showing its
  schema vocabulary is closed.
- `render_summary_body` gains a new section without changes to its size-accounting
  or drop loop, demonstrated by SPEC-45 and SPEC-46 landing as descriptor additions.
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
- `ai-review/tests/unit/test_body_hash.py` — add
  `test_truncation_drops_whole_literal_span_instead_of_splitting_it` and
  `test_required_scalar_that_redacts_to_empty_renders_owned_placeholder`.
- `ai-review/tests/unit/test_schema_validation.py` — add
  `test_renderer_exempt_enums_are_closed_vocabularies`, asserting every value rendered
  outside the literal API is constrained by an `enum` in
  `finding_batch.schema.json`. This is what keeps the narrowed boundary honest if a
  schema is ever loosened.
- `ai-review/tests/unit/test_post.py` — add
  `test_summary_uses_literal_renderer_for_reviewer_path_title_evidence_and_suggestion`,
  `test_malformed_suggestion_fence_is_rendered_not_dropped`, and
  `test_summary_section_descriptors_drop_by_declared_priority` covering a synthetic
  third section so the generic drop loop is verified independently of SPEC-45/46.
- `ai-review/tests/security/test_prompt_injection_rendering.py` — add
  `test_renderer_owned_details_block_keeps_fenced_model_text_literal`, asserting the
  blank line after `</summary>` and that model text cannot close the disclosure
  element.
- `ai-review/tests/contract/test_golden_consensus.py` — refresh the inline and
  summary rendering goldens, including an adversarial fence-escape fixture.
- Cross-platform integration coverage in
  `ai-review/tests/integration/test_post_gate_e2e.py` — post the same hostile group
  through `gitlab_discussions` and `github_reviews`, then assert no extra thread,
  no marker loss, and deterministic unchanged-on-rerun behavior.
