# SPEC-45 — Critique provenance and tiered critique display

- **Severity:** Low-Medium. Maintainers see disputes but not duplicate or noise reasoning. The original motivating problem, groups suppressed by majority noise disappearing from the review, is solved by [SPEC-67](spec-67-majority-noise-to-fyi.md). · **Effort:** Part A S, Part B S–M · **ROI rank:** post-2.0.2
- **Depends on:** Part A, nothing. Part B, the next major consensus cutover (`consensus.v3`) shared with S05. SPEC-44 literal-safe rendering is done: it shipped in 60b9357, with the wrapping code-span follow-up in 45b891f.

## Decision note

On 2026-10-13 the maintainer settled S11: majority-noise groups are **demoted to FYI**
rather than dropped ([SPEC-67](spec-67-majority-noise-to-fyi.md)). Nothing is hidden any more, so this specification no
longer carries a `drop_reason`, a majority-noise disposition audit, or
`critique.show_disposition_audit`. SPEC-67 also amends
[ADR-0002](../decisions/0002-post-1.0-review-output-policy.md) decision 3, the opt-in
audit, which this specification no longer implements. ADR-0002 decision 2, tiered
critique display with full retention, still governs everything below.

## Current state

Nothing in this specification is on `main`. Consensus retains only
`critique_disputes` (`_apply_critiques` in `critique.py`), and `render.py` renders it as
a `Dissent:` section.

An earlier implementation exists on the unmerged branch
`spec-45-critique-provenance` (commit 62d9934, 2026-07-30). It is 133 commits behind
`main` and predates SPEC-54/55, the extraction of `critique.py`, and the split of
summary rendering into `summary_render.py`. Do not rebase it. Its reducer tests,
golden cases, and body-hash tests are worth mining.

## Rationale

Valid duplicate decisions and noise rationales are discarded by consensus today, so a
maintainer cannot tell who challenged a concern or why a duplicate link was accepted.
This specification retains that reasoning without changing the authority model:
critique remains a modifier of existing grouped findings only.

Per-group provenance is this specification's job. Per-run aggregates belong to
[SPEC-66](spec-66-run-health-summary.md#critique-coverage-absorbed-from-spec-60).

**Retention and display are separate decisions.** The artifact records every selected
effective non-agree critique in full. Display is budgeted by value per line:

| Verdict | Value to a maintainer reviewing the MR | Display |
| --- | --- | --- |
| `dispute` | High — a counterargument that should change whether they act. | Full rationale. |
| `noise` | Moderate — real "not worth your time" dissent, but often one dismissive clause. | Elided to one line. |
| valid `duplicate` | Near zero — the group is already merged and `contributing_reviewers` names every reporter. | Counts only. |
| `agree` | Already carried by the support counter. | Counts only, as today. |

On a panel of N reviewers a group can have up to N−1 critics, and each rationale is a
multi-line prose paragraph, so rendering everything expanded would dwarf the finding.

## Delivery split

Replacing `critique_disputes` is breaking: `consensus.schema.json` sets
`additionalProperties: false`, and the post stage validates against the current
schema constant. A compatible subset needs no schema change, because it reads only
fields `consensus.v2` already carries. Ship it first.

### Part A — compatible minor release (S)

Uses only `critique_summary`, `critique_disputes`, and `contributing_reviewers`.

1. **Counts line.** In inline bodies, replace the `Dissent:` section with an
   always-visible counts line derived from `critique_summary`. Separators and labels
   are renderer-owned; zero-count verdicts are omitted; the line is omitted when the
   group has no critiques.
2. **Disclosure.** Disputes from `critique_disputes` move behind a collapsed
   renderer-owned `<details>` element, with full rationale and an optional
   `adjusted_severity` span. The element is omitted when there is no dispute.
3. **`Found by`.** Add `Found by`, derived solely from the sorted
   `contributing_reviewers`, to **summary entries only** (`_summary_line` in
   [`summary_render.py`](../../ai-review/src/ai_review/summary_render.py)). Inline
   bodies already list the same reviewers under `Support:` / `- Direct reviewers:`.
   SPEC-67 annotates demoted FYI entries with their noise count; place `Found by`
   before that annotation.
4. **Renderer version.** Set `RENDER_BODY_VERSION` to `render-body.v5`. That causes
   one refresh wave, and Part B must not cause another (see Part B).

````markdown
Critique: 2 agree · 1 dispute · 1 duplicate · 1 noise

<details>
<summary>Critique detail (1 dispute)</summary>

- `reviewer-x` disputes: (suggested severity: `minor`)
  `The proposed path is unreachable after the new guard.`

</details>
````

### Part B — next major consensus cutover (S–M)

Ships in `consensus.v3` with S05. No old-version decoder, no dual path, and no
temporary-compatibility entry: consensus artifacts are per-run, and a v3 post stage
reads only the v3 artifact its own consensus job produced.

1. **`critique_observations` replaces `critique_disputes`.** It is a required group
   property, `[]` when empty. `critique_disputes` and the `CritiqueDispute` TypedDict
   are deleted, not kept as a projection.
2. **Noise.** Inside the existing disclosure, each noise observation renders as one
   line: critic span, a renderer-owned verdict label, and the rationale as one
   `literal_span` elided to the shorter of its first sentence or 200 characters, with
   a renderer-owned `…`. Elision is measured on the normalized value before span
   wrapping.
3. **Valid duplicate.** Counts only. Rationale and target are retained, not rendered.
4. **No version bump.** A group whose observations are only disputes must render
   byte-identically to Part A. Bodies then change only where noise observations add
   content, and those threads update through their own body hash.

```json
{
  "critic": "opencode",
  "verdict": "dispute",
  "rationale": "The changed caller already rejects an empty value.",
  "adjusted_severity": "minor",
  "duplicate_of_source_finding_id": null
}
```

- Required: `critic`, effective `verdict` (`dispute`, `duplicate`, or `noise`), and
  nonblank `rationale`.
- `adjusted_severity` is optional, retained only under the existing effective-dispute
  semantics.
- `duplicate_of_source_finding_id` is present only for an effective, validated
  `duplicate`.

Observations are recorded from the existing selection in
[`critique.py`](../../ai-review/src/ai_review/critique.py), not re-derived:

- direct contributors (`contributing_reviewers`) are excluded;
- `_effective_verdict` turns an invalid `duplicate` into `dispute`, which then keeps
  no duplicate target;
- `_collapse_critiques` reduces one critic's critiques of one group to a single
  verdict by `noise > dispute > duplicate > agree`, breaking ties by the lowest
  `_critique_sort_key`.

A selected critique whose critic or rationale is blank still counts in
`critique_summary` but is not retained, as today. Sort observations by
`(critic, verdict, duplicate_of_source_finding_id or "", adjusted_severity or "",
rationale)`; this is also the render order. `critique_summary`, support counts,
duplicate links, severity inputs, and decisions are unchanged.

## Literal safety

Every model value renders through the literal API in
[`render.py`](../../ai-review/src/ai_review/render.py): `literal_span` for one-line
values and the wrapping prose form (`_prose_fragment`, one code span per line) for
rationale. The `<details>`/`<summary>` tags and the summary label are the only
bot-owned additions, so model text can neither close the element nor open a new one.
The deleted SPEC-44 document once permitted this element; this specification is now
the authority, and a hostile-input test must prove it.

The disclosure is one atomic fragment for `limit_body_before_marker`. Place it after
the suggestion: `_fit_fragments` stops at the first fragment that does not fit, so an
oversized disclosure placed earlier would evict everything behind it. The `Support:`
footer stays in the reserved suffix.

## Exact implementation surface

- **Part A:** `render.py` (counts line, disclosure, `render-body.v5`) and
  `summary_render.py` (`Found by`). No consensus, schema, configuration, or posting
  change.
- **Part B:** `critique.py` records observations in the `(group, critic)` loop of
  `_apply_critiques`, replacing the `critique_disputes` append; `consensus.py`
  initializes `critique_observations: []`; `types.py` and
  [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json) replace
  the old property; `render.py` reads observations and renders noise lines.
- Each part updates the [consensus reference](../reference/consensus.md), the
  [artifacts reference](../reference/artifacts-and-schemas.md) where the schema
  changes, and CHANGELOG in its implementation change.

## Acceptance criteria

- **Part A:**
  - A group with critiques adds exactly one visible line by default.
  - Expanding the disclosure shows full dispute rationale.
  - Summary entries show `Found by`; inline bodies do not, and `Support:` is
    unchanged.
  - Consensus artifacts are byte-identical to today.
- **Part B:**
  - Every group carries a deterministic `critique_observations` array.
  - Invalid duplicates appear as disputes.
  - Noise appears only in its elided one-line form.
  - Dispute-only groups render byte-identically to Part A.
  - Decisions are unchanged.
- **Both:**
  - Model text inside the disclosure cannot inject Markdown or HTML.
  - Identical inputs produce identical artifacts, rendered bytes, and hashes.

## Required tests

- `ai-review/tests/unit/test_body_hash.py` (Part A) —
  `test_critique_counts_line_is_the_only_addition_without_disputes` and
  `test_renderer_owned_details_keeps_model_text_literal`, using hostile rationale
  containing `</details>`, `<summary>`, backticks, and HTML comments.
- `ai-review/tests/unit/test_summary_render.py` (Part A) —
  `test_summary_entry_shows_found_by_and_inline_body_does_not`.
- `ai-review/tests/unit/test_consensus_reducer.py` (Part B) —
  `test_critique_observations_keep_selected_effective_non_agree_verdicts`,
  `test_observation_follows_strongest_verdict_when_one_critic_has_several_critiques`,
  `test_valid_duplicate_observation_keeps_target`, and
  `test_invalid_duplicate_observation_is_a_dispute_without_target`.
- `ai-review/tests/unit/test_body_hash.py` (Part B) —
  `test_noise_rationale_elision_is_deterministic_at_sentence_and_length_bounds` and
  `test_dispute_only_group_renders_identically_to_part_a`.
- `ai-review/tests/unit/test_schema_validation.py` and
  `ai-review/tests/unit/test_types_schema_alignment.py` (Part B) — the required
  observation array, its optional fields, and rejection of a v3 group carrying
  `critique_disputes`.
- `ai-review/tests/contract/test_golden_consensus.py` (Part B) — add valid duplicate,
  invalid duplicate, and noise cases to `tests/contract/golden_cases.py` (fixtures in
  `tests/fixtures/golden/`, regenerated with
  `tests/contract/update_golden_consensus.py`), each rerun in a different batch order.
  Branch `spec-45-critique-provenance` has earlier versions of these cases.
