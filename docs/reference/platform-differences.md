# Platform differences

| Concern | GitLab | GitHub |
|---|---|---|
| Installation | Protected direct include or hardened child pipeline | Checked-in Actions workflow |
| Trusted workflow boundary | Protected template project/ref and variable boundary | Base-branch workflow selected for `pull_request`; never `pull_request_target` |
| Review target | Merge request | Pull request |
| Inline posting | Discussions/DiffNotes | Pull-request review comments |
| Summary and state | MR notes; state author must match token bot | PR issue comments; state author must match configured bot login |
| Commands | Reply in finding discussion; Developer/30+ | Reply to root inline comment; user-repository `OWNER`, or Write/Maintain/Admin verified with a fine-grained token (effectively required for organization repositories) |
| Thread resolution | GitLab discussion API | GraphQL; optional fine-grained resolve token |
| Merge enforcement | **Pipelines must succeed** | Gate job configured as required check |
| Fork behavior | Protected variables withheld; deployment topology determines whether trusted jobs run | External forks skipped by the canonical workflow |
| Concurrency | Post serialized with an MR-scoped resource group | Workflow concurrency groups by PR; in-progress runs are not cancelled |
| Diff collection | Paginated MR diff API; exact-path raw recovery for collapsed entries, with incomplete fallbacks rejected | Immutable base/head comparison raw diff; HTTP 406/too-large rejected |
| Artifact retention | 7 days for prepare/review/critique; 30 days for consensus/post/gate | Repository/organization Actions default |

Both platforms use the same configuration, reviewer adapters, artifact schemas,
consensus policy, posting reconciliation, and gate evaluator. Platform-specific
credentials are never passed into reviewer subprocess environments.

## Rendered review output

The shared posting renderer uses `render-body.v3`. Model-authored titles, paths,
reviewer names, bodies, evidence, critique text, and suggestions are displayed as
literal data on both platforms. The invariant is structural and single:
**every model-authored value renders inside a `code` or `pre` element.** Redaction and
newline normalization happen before that encoding; malformed suggestion fences remain
visible as literal data. Only renderer-owned labels, the validated
severity/category/decision presentation, and bot markers are Markdown structure.

That invariant is what makes the boundary hold, and it does more than protect layout.
Both platforms render Markdown and then run a DOM filter pipeline over the result —
autolinker, `@` mentions, issue/label/epic references, emoji — and those filters skip
only `a`, `code`, `kbd`, `pre`, `script`, and `style` subtrees. On GitLab two of them
have write side effects, creating notifications and cross-reference notes, so model
text that escaped its `code` element could act *outside* the review comment rather
than merely restyle it. A container chosen for readability alone (a raw-HTML
`blockquote` or an escaped paragraph) satisfies CommonMark and still fails here, and
re-arguing it against each new platform filter is exactly the open-ended completeness
problem the literal renderer exists to avoid.

Within that invariant the renderer picks the container by content shape:

| Value | Container | Why |
|---|---|---|
| Titles, paths, reviewer names, severities | code span | scalars; already wrap |
| Bodies, evidence, critique rationale | prose paragraph of one code span per line, joined by backslash hard breaks | inline `code` is an inline element, so long prose wraps instead of scrolling horizontally |
| Suggestions | `text` fenced block | code, where monospace columns and horizontal scroll are correct |

A blank model line renders as a line holding only the hard break: a genuinely empty
line would end the paragraph and orphan the fragments after it. The last line never
carries a break, which would otherwise render its backslash literally.

The renderer uses dynamic delimiters and fragment-aware limits so spans are never
split, blocks are always closed, prose is shortened only by re-encoding a shortened
final span, and the trusted footer and marker remain outside truncated model content.
Because a code span owns two delimiters, the longest span that fits an arbitrary
budget can leave a character of the platform limit unused; the guarantee is that the
comment never exceeds the limit, not that it fills it exactly. When a scalar begins or
ends with a backtick or a space, the renderer adds the standard one-space code-span
padding on both sides; the padding is display syntax, not part of the normalized
value. Scalar length caps are applied before newline encoding, delimiter selection,
and that padding, while the platform limit applies to the final rendered comment.
Inline `body_hash` also includes the canonical source finding hash as a
renderer/marker identity input, so a same-looking finding from a different source set
still refreshes its existing bot-owned discussion.

Backslash-escaping prose was considered and rejected. It would preserve proportional
text flow, but it would require a supposedly complete list of Markdown, math, HTML,
autolink, and reference-link constructs that differs between GitHub and GitLab — and
escaping Markdown constructs does nothing about the post-render filters above, since
escaped prose still lands in a paragraph with no ignored ancestor. Keeping every value
inside a `code` element is the safer structural boundary, and it costs only monospace
presentation rather than proportional flow.

The v3 renderer pre-lands an atomic renderer-owned `<details>` compositor for the
future SPEC-45 disclosure sections. Its summary text is renderer-owned, its content
must already be literal fragments, and it emits a blank line after `</summary>` so
GitHub recognizes a following fenced block. `render_body` does not call this helper
in v3; SPEC-45 owns the later disclosure activation, and v3 does not add critique
sections to the current summary output.

The consensus input category is now the same closed enum as the finding batch
(`security`, `correctness`, `performance`, `maintainability`, `style`, `test`, or
`other`). Hand-edited or third-party consensus artifacts using an arbitrary category
are therefore rejected at posting; pipeline-produced artifacts remain compatible.

GitLab's deprecated `/changes?access_raw_diffs=true` endpoint is a conditional
compatibility fallback only; the paginated `/diffs` endpoint remains primary.
Fallback data is accepted only when the response explicitly reports
`overflow=false`, every affected old/new path has one exact match, and the
replacement has no `collapsed` or `too_large` flag. Prepare re-fetches the MR
diff version afterward and rejects any base, start, or head revision change.
