# Archived — Unanchored Review Advisories

Status: paused indefinitely on 2026-10-13. Historical identifier: SPEC-46. The
full proposal was deleted from `docs/improvement-specs/` and remains in git history
(`docs/improvement-specs/spec-46-unanchored-advisories.md` at `da35afa`).

## Intent

Let reviewers report concerns that have no defensible changed-hunk line, such as a
missing migration, a cross-file design problem, or an absent test. Each concern
would be a summary-only advisory attributed to its reviewer, with no consensus
vote, no thread, and no lifecycle. That avoids losing the concern or forcing it
onto a misleading line.

## Why it is paused

- It adds a second model-output kind and a new public output class: single-reviewer
  content posted without grouping or critique. Neither is among
  [ADR-0003](../../docs/decisions/0003-product-invariants-and-complexity-envelope.md)'s
  accepted dimensions.
- The simplification audit (S19) recommends freezing it.
- With SPEC-67 demoting majority-noise groups to FYI, the summary already carries
  more content. Unreviewed single-reviewer text would add noise before critique of
  advisories exists.

## Resume conditions

- An accepted ADR admits the output class.
- The S05 (model contract), S06 (result envelope) and S13 (absence-based
  resolution) decisions are settled, and the next major artifact cutover is
  planned.
- The contract is re-derived against the post-S05 authoring schema and the post-S06
  result envelope, not patched from the historical text. Re-check ADR-0002
  decisions 4–5 (the advisory cap and scope-path handling) at the same time.
- The name does not collide with the existing "Advisory (FYI) findings" summary
  section.

This plan is independent of every other archived plan.
