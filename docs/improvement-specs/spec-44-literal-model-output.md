# SPEC-44 — Literal-safe rendering of model output

- **Severity:** High (untrusted model output can alter the review that a maintainer sees) · **Effort:** L (raised from M — see [Deviations](#deviations-from-the-original-draft)) · **ROI rank:** post-1.0
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
value" — a rule of "every dynamic value" would either be violated by the existing
severity header or would force closed enumerations through a literal API that buys no
safety.

**The exemption is earned per field, and only where a validation boundary actually
enforces it.** A value may render outside the literal API only when the schema of the
artifact the renderer reads closes its vocabulary **and** that schema is validated
before rendering. For inline and summary rendering the artifact is `consensus.v1`,
because `render_body` and the summary-entry renderer consume consensus groups.

Two conditions must hold, and today **neither is fully satisfied**:

| Rendered field | Vocabulary closed by `consensus.schema.json`? | Validated before rendering? |
| --- | --- | --- |
| `final_severity` | Yes (enum) | **No** |
| `decision` | Yes (enum) | **No** |
| `category` | **No** (`{"type": "string", "minLength": 1}`) | **No** |

On the vocabulary axis, `category` is enumerated in `finding_batch.schema.json` and the
pipeline copies it from a validated finding, so the in-process path is safe by
construction — but a consensus artifact loaded from disk can carry any string.

On the enforcement axis, the posting stage does not validate its input at all.
[`post.py`](../../ai-review/src/ai_review/post.py)'s `cli` does
`consensus = cast(Consensus, load_json_file(args.consensus))` — a `cast` is a typing
annotation with no runtime effect — and goes straight to `post_consensus`, which renders
`group['category']` into Markdown at
[`render.py`](../../ai-review/src/ai_review/render.py). It then validates its *output*
against `post_result.schema.json`. The consensus stage validates
(`validate_consensus_inputs`) and the gate stage validates
([`gate.py`](../../ai-review/src/ai_review/gate.py) `cli`), so posting is the sole
consumer that trusts the artifact — and it is the one that renders to the platform.

**This is a pre-existing gap, not one introduced here.** In the current product an altered
consensus artifact — including one today's schema accepts, since `category` is
unconstrained — can place arbitrary text in the severity header, category, and consensus
footer, and can supply wrong-typed `vote_count` / `block_merge` values, none of which the
literal API would cover. This specification therefore closes both conditions: it tightens
`consensus.schema.json` *and* requires the posting stage to validate before any posting
API call. Ratified in
[ADR-0002](../decisions/0002-post-1.0-review-output-policy.md).

**What validation does not buy.** Schema validation is a shape-and-vocabulary check. It
cannot reject a schema-valid but altered artifact and it establishes nothing about
provenance, so it must never be cited as a reason to trust a value's content. Its role
here is narrow and singular: to make the exempt enumerations genuinely closed at the point
of use. Every other rendered value is protected from **Markdown, marker, and layout
injection** because it is literal, not because the artifact was validated — which is why
the exemption remains confined to closed enumerations and is never extended to a free-text
field on the strength of validation.

**What literal rendering does not buy either.** Literal rendering constrains only how a
value is *displayed*. It has no effect on a value's *semantics*, and neither mechanism in
this specification addresses artifact integrity. A schema-valid altered
`summary.block_merge` still decides the merge gate in
[`gate.py`](../../ai-review/src/ai_review/gate.py); a `decision` change still moves a
finding between inline, summary, and dropped. Those consequences are out of scope here and
are not claimed to be mitigated.

## Design decision: code-element containment over selective escaping

Several mechanisms can make a dynamic value literal. This specification requires
that **every model-authored value render inside a `code` or `pre` element**, and
records the alternatives so the choice is not revisited by accident.

**Chosen — wrap in renderer-owned code spans/fences.** Safety is structural on two
levels. The delimiter is computed from the value, so correctness does not depend on
enumerating Markdown constructs. And the resulting element is one both platforms'
post-render DOM filters ignore — see the rejection of raw-HTML containers below,
which is the sharper reason this boundary and not a prettier one.

Within that requirement the container is chosen by content shape. Prose (`body`,
evidence, critique rationale) renders as a paragraph of one code span per line,
joined by renderer-owned backslash hard breaks: inline `code` is an inline element,
so long prose wraps at spaces instead of scrolling horizontally. Suggestions keep
the `text` fenced block, because they are code and monospace columns with horizontal
scroll are correct for them. The residual cost is monospace presentation and
flattened model emphasis, not lost proportional flow. SPEC-45's
disclosure-collapsed sections remain the mitigation for vertical bulk.

**Rejected — a raw-HTML container (`blockquote`/`p`) with HTML-escaped text.** This
looks like the strongest form of the escaping argument, because inside HTML
character data only `<` and `&` are structurally meaningful — a closed,
spec-defined, platform-independent set — and a single-line container cannot be
terminated early by a blank line. The CommonMark reasoning holds; the platform
reasoning does not. Both GitHub and GitLab render Markdown and then run a DOM
filter pipeline over the *result*, where a raw-HTML block's text nodes are
indistinguishable from Markdown-derived ones. Those filters skip only `a`, `code`,
`kbd`, `pre`, `script`, and `style` ancestors, so inside a `blockquote` GitLab's
autolinker, reference filters, and emoji filter all act on model text — and the
`@mention` and `#123`/`!45` reference filters have **write side effects**, creating
notifications and cross-reference notes. That escapes the comment entirely, which is
worse than layout injection. Numeric character references do not help: the HTML
parser decodes them before the filters run. The completeness list would simply move
from character classes to filter classes, and would have to be re-argued for every
filter either platform adds.

**Rejected — backslash-escape prose, fence only code-shaped fields.** This
preserves proportional text flow and reads better. It is rejected because
correctness becomes a completeness argument over an open-ended construct list
(line-leading `#`, `>`, `-`, `*`, `+`, `N.`, `|`, plus backticks, `$` math,
autolinks, raw HTML, and reference-link syntax), and that list differs between
GitHub and GitLab. A single missed construct is a silent injection. It also fails
the filter problem above for the same reason the raw-HTML container does: escaped
prose lands in a paragraph with no ignored ancestor, and escaping `#` and `>` does
nothing about `@all`. Escaping may be reconsidered only if it is expressed as a
proven-complete transform with per-platform fixtures *and* it accounts for
post-render filters, not as a hand-maintained character list.

**Rejected — hard-wrapping prose at a fixed column inside the fence.** Cheap and
structurally inert, but lossy: what the maintainer reads would no longer equal
`consensus.groups[].body`, the wrap would be baked into `body_hash`, and any later
change to the width would force another refresh wave across open merge requests.

## Scope

**In:** the common rendering path for inline threads and summary comments; every
visible free-text or path-shaped value (model text, reviewer names, paths, titles,
bodies, evidence, critique rationale, and suggestions); GitHub and GitLab
platform-size handling; rendering tests and golden fixtures.

**Out:** changing consensus, voting, redaction rules, reviewer prompting, or allowing a
supported subset of model-authored Markdown. This is not a formatting-preference
feature: model-originated free text is never partially trusted Markdown. Also out:
routing closed enumerations through the literal API (see the boundary statement in the
rationale).

**In, narrowly:** two changes the rendering exemption depends on — tightening
`consensus.schema.json` `$defs.group.properties.category`, and adding consensus-schema
validation to the posting stage. No other schema meaning changes and no
`schema_version` is bumped.

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

   Per the boundary statement in the rationale, a field is outside this API only once
   both its vocabulary is closed by `consensus.schema.json` and the posting stage
   validates against that schema: `final_severity`, `decision`, and — once tightened by
   this specification — `category`. The existing `**AI review: MAJOR correctness**`
   header line is therefore retained verbatim. Until the posting-stage validation lands,
   the exemption is not earned and these fields render through `literal_span`.
3. Multiline values use one of two containers, and **both are `code` elements** —
   that containment, not the specific container, is the invariant this
   specification enforces.

   `prose_block` is used for the readable multiline values: body, evidence, and
   critique rationale. It emits one code span per source line, each with a
   delimiter of `longest_backtick_run(line) + 1` backticks, joined by
   renderer-owned backslash hard breaks. Inline `code` is an inline element, so
   the paragraph wraps at the comment width instead of scrolling horizontally. A
   blank source line contributes no span, leaving a line that holds only the hard
   break: a genuinely empty line would end the paragraph and orphan every
   fragment after it. The final line never carries a break, which CommonMark
   would otherwise render as a literal backslash. Prose under a `- ` label is
   indented to that item's content column; lazy continuation would keep it in the
   item regardless, since a prose line always begins with a backtick or the hard
   break, but indenting matches the summary renderer.

   `literal_block` is used for `suggestion`. It emits a `text` fenced block using
   `max(3, longest_backtick_run(value) + 1)` backticks for both delimiters.

   In both cases the delimiters, surrounding newlines, indentation, and field
   labels are renderer-owned, so a model-supplied fence can never close the outer
   container.
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

The following is actual renderer output for a hostile body, and must be regenerated
from the renderer rather than hand-edited. Each source line becomes its own span;
the four-backtick delimiters are chosen per line because those lines contain a
three-backtick run. The heading, quote, list, math-looking text, and
comment-looking text all remain literal, and the whole paragraph wraps.

`````markdown
**AI review: MAJOR correctness**

Title: `PHP template output is literal`

Body:
`# not a heading`\
`> # not a quoted heading`\
`- not a list`\
```` ```php ````\
`echo "$total"; // $not_math$`\
```` ``` ````\
`< !-- not a bot marker -- >`
`````

The same field must have the same literal treatment in an inline discussion and in
the summary comment. A summary may use renderer-owned headings and entry lists, but
it must not rely on a blockquote or a raw one-line interpolation to make model text
safe.

### Suggestions are data, not executable Markdown

Retire the posting decision that suppresses a suggestion because its
model-supplied triple-backtick count is unbalanced. The obsolete
`validate_suggestion` helper is removed; malformed suggestions are never filtered by
fence balance. A suggestion is rendered through `literal_block`; malformed inner
fences, HTML-comment-like text, and Markdown directives are visible as text rather
than interpreted as syntax.

Unlike a body, a suggestion keeps the fenced block rather than the wrapping prose
paragraph, because it is code: monospace columns and horizontal scroll preserve its
meaning, and it is the field most likely to carry long unbreakable tokens that would
not reflow in any container. Both forms satisfy the same containment invariant.

### Limits, hashes, and markers

Keep the platform limits from SPEC-26: 1,000,000 characters for GitLab discussions
and 65,536 for GitHub review comments. Apply the limit to the final literal-rendered
payload, before the bot marker and before `body_hash` is calculated.

Replace the current raw backtick-count truncation heuristic with renderer-owned
fragments or an equivalent token stream. It must never infer safety from model text.
Truncation obeys three fragment rules:

- A cut may land inside a `literal_block`. The renderer then appends that block's
  exact owned closing delimiter before the existing platform-truncation notice,
  trusted footer, and marker.
- A cut may **never** land inside a rendered `literal_span`. Spans are atomic: a
  partially rendered span would leave an unmatched backtick run and is never
  emitted. For a standalone span, truncation falls back to the preceding fragment
  boundary and drops the whole span with its bot-owned label.
- A cut may land inside a `prose_block`. Whole lines are kept, and the final
  surviving line is **re-encoded from a shortened prefix of its source scalar**
  with a freshly recomputed delimiter and padding — never by cutting the rendered
  span, because a cut can land inside a backtick run and leave the closing
  delimiter ambiguous. That re-encoding is what keeps a single unbroken paragraph
  from losing all of its content. The renderer then drops the hard break the
  removed remainder was breaking to, and the notice is separated by a blank line
  because a prose paragraph does not end at a single newline.

  Selecting the shortened prefix must not assume the encoded length grows with the
  prefix: the delimiter widens with the longest backtick run, and boundary padding
  appears and disappears as the final character changes. Stepping back by the
  observed overshoot converges to nothing on backtick-dense text and drops the
  field entirely.

Because a span owns two delimiters, the longest one that fits an arbitrary budget
may leave a character of the platform limit unused. The guarantee is that a rendered
comment never exceeds its limit, not that it fills it exactly.

Summary comments continue to drop whole rendered entries and append their existing
size-limit trailer; they never cut an entry, a span, or a literal fence in half.

`body_hash` and the summary hash are calculated from the final redacted,
normalized, literal-rendered, size-limited body. Inline `body_hash` additionally
includes the canonical source-finding hash as a renderer/marker identity input, not
raw model text. Equal inputs must therefore produce identical bytes and hashes on both
platforms, while a same-looking group with a different source set still refreshes its
existing discussion. Redaction, newline normalization, marker parsing, source hashes,
and idempotent upsert behavior remain unchanged.

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
5. In [`ai-review/src/ai_review/post.py`](../../ai-review/src/ai_review/post.py), validate
   the loaded consensus artifact against `consensus.schema.json` in `cli`, **before
   `create_runtime_platform` and therefore before any posting API call**. Mirror the
   established pattern in [`gate.py`](../../ai-review/src/ai_review/gate.py) `cli`
   exactly — `load_json_file`, `validate_instance(consensus, "consensus.schema.json")`,
   then `cast` — so the three stages that consume a consensus artifact treat it
   identically. The CLI is the correct boundary because it is the only place an
   artifact file enters; in-process callers construct their own values.

   Validation must precede client construction so a schema-invalid artifact cannot cause
   a partially posted review before being rejected. This item is a prerequisite for the
   enum exemption, and it independently closes the pre-existing gap described in the
   rationale.
6. In
   [`ai-review/schemas/consensus.schema.json`](../../ai-review/schemas/consensus.schema.json),
   tighten `$defs.group.properties.category` from `{"type": "string", "minLength": 1}` to
   the `finding_batch.schema.json` category enum (`security`, `correctness`,
   `performance`, `maintainability`, `style`, `test`, `other`). Keep
   `schema_version` at `consensus.v1`; no artifact migration exists because the value
   has always been copied from a schema-validated finding
   (`_representative(findings)["category"]`), so every artifact the pipeline has produced
   already satisfies the enum. The intended effect is that a hand-edited or third-party
   consensus artifact with an arbitrary category is rejected by every consensus-consuming
   stage — including, once item 5 lands, the posting stage — instead of reaching the
   renderer.
7. Refresh rendering goldens, body-hash fixtures, and marker-parser fixtures. The
   parser must continue to recognize both existing v2-rendered bot markers and v3
   markers because the marker grammar itself does not change. No consensus, finding,
   or state schema version changes are required for this specification.
8. When implementation lands, update the current rendering/reference documentation
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

The `category` schema tightening needs no migration either — the value has always been
copied from a schema-validated finding — but it does narrow an accepted input, and the
new posting-stage validation will reject an artifact that previously posted. Both are
intended.

Rollback couples three things: the enum exemption, the tightened constraint, and the
posting-stage validation are a single decision. A rollback that restores the loose
`{"type": "string"}` constraint, or that removes the posting-stage validation, must also
restore `literal_span` rendering for `category`, `decision`, and `final_severity`.
Reverting one without the others reopens the injection surface.

## Deviations from the original draft

These requirements changed after the first committed draft of this specification. The
rendering-boundary decision was ratified in
[ADR-0002](../decisions/0002-post-1.0-review-output-policy.md); the rest are defect fixes
or the scope consequences that follow from them.

| Original requirement | Now | Reason | Decided in |
| --- | --- | --- | --- |
| "Every dynamic value must be literal data" | Every free-text or path-shaped value; closed enumerations exempt, and only where a validation boundary enforces the vocabulary | An unqualified rule would either be violated by the existing severity header or force closed enums through an API that buys no safety. The exemption is earned per field rather than assumed. | ADR-0002 §1 |
| No schema changes | Tightens `consensus.schema.json` `$defs.group.properties.category` to the finding-batch enum | The exemption depends on it. `consensus.v1` left `category` an unconstrained string, so the exemption was unearned at the boundary the renderer reads. | ADR-0002 §1 |
| Posting-stage input trust unexamined | Posting stage must validate the consensus artifact before constructing a platform client | `post.py`'s `cli` casts the loaded artifact with no runtime check, unlike the consensus and gate stages. Without this the schema tightening buys nothing on the rendering path. | n/a — closes a pre-existing gap |
| **Effort: M** | **Effort: L** | Scope grew with the three rows above plus two items absent from the draft: converting the platform-limit API to renderer-owned fragments, and restructuring `render_summary_body` into section descriptors so SPEC-45 and SPEC-46 add sections as data. With the goldens, body-hash, marker-parser, schema, and zero-network posting regressions, this is no longer an M. | n/a — estimate follows scope |

## Acceptance criteria

- No visible free-text or path-shaped value can create a heading, quote, list, math
  rendering, HTML comment, marker, or code-fence boundary in either supported
  platform. Closed enumerations are out of scope by the stated boundary, and a schema
  test asserts every exempt field is closed **by `consensus.schema.json`** so the
  exemption stays earned rather than assumed.
- A title containing backticks and a multiline field containing arbitrary backtick
  runs render literally without corrupting the bot footer or marker.
- Redaction and newline normalization still occur before display; a malformed
  suggestion is shown safely rather than silently omitted.
- Inline bodies and summary entries respect their existing platform limits, retain
  the trusted marker, and have stable hashes across identical reruns.
- Truncation never emits a partially rendered literal span, and never leaves a
  literal block unclosed.
- Exempt enums keep their existing renderer-owned presentation; the
  `**AI review: <SEVERITY> <category>**` header is unchanged.
- The posting stage rejects a consensus artifact that fails `consensus.schema.json`
  **before** constructing a platform client, so a schema-invalid artifact cannot produce a
  partially posted review. Out-of-vocabulary `category`, `decision`, and `final_severity`
  values are all rejected on that path. A schema-valid alteration is not rejected — that
  is not what this check is for.
- Any future field added to a rendered payload is literal by default; exempting one
  requires showing that the rendered artifact's own schema closes its vocabulary *and*
  that the consuming stage validates against it.
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
  `test_renderer_exempt_enums_are_closed_in_consensus_schema`, asserting every field
  rendered outside the literal API (`final_severity`, `decision`, `category`) is
  constrained by an `enum` in **`consensus.schema.json`** — the artifact the renderer
  reads — not merely upstream. This is the check that keeps the narrowed boundary honest
  if a schema is ever loosened, and it fails today against the untightened `category`.
  Add `test_consensus_group_category_outside_enum_is_rejected` for the schema itself, and
  assert `category` parity between `consensus.schema.json` and
  `finding_batch.schema.json` so the two cannot drift.
- `ai-review/tests/unit/test_post.py` — add
  `test_posting_cli_rejects_schema_invalid_consensus_before_client_construction`. Feed a
  consensus artifact with an out-of-vocabulary `category` (Markdown-injecting text), then
  separately an invalid `decision` and `final_severity`, through `post.py`'s `cli`.

  **Patch `create_runtime_platform` itself with a mock and assert it was never called.**
  A platform double cannot prove the negative: injecting a double means a client object
  *was* constructed, so the double can only show that no method was invoked on it. The
  claim under test is that construction never happens, and only asserting non-invocation
  of the factory establishes that. Also assert each run raises the schema error and that
  no output artifact is written.

  This is the regression that makes the exemption enforceable rather than assumed. Without
  it, a refactor could move validation after client construction — or after the first API
  call — and nothing would notice.
- `ai-review/tests/unit/test_post.py` — cover literal rendering of summary paths,
  titles, and bodies, plus
  `test_summary_section_descriptors_drop_by_declared_priority` with a synthetic
  third section so the generic drop loop is verified independently of SPEC-45/46.
  Evidence, critique, and suggestion values remain covered on the inline literal
  rendering path; SPEC-45 owns any later summary disclosure coverage.
- `ai-review/tests/security/test_prompt_injection_rendering.py` — add
  `test_renderer_owned_details_block_keeps_fenced_model_text_literal`, asserting the
  blank line after `</summary>` and that model text cannot close the disclosure
  element.
- `ai-review/tests/contract/test_golden_consensus.py` — add the exact
  `render_body_hostile.json` rendering-contract fixture, including hostile fences,
  HTML-close tags, marker-looking text, and the expected body/hash output.
- Cross-platform integration coverage in
  `ai-review/tests/integration/test_post_gate_e2e.py` — post the same hostile group
  through `gitlab_discussions` and `github_reviews`, then assert no extra thread,
  no marker loss, and deterministic unchanged-on-rerun behavior.
