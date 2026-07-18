# SPEC-25 — Propagate suggestion/evidence into consensus groups; surface dispute rationale

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** Medium (validated reviewer content silently discarded) · **Effort:** M · **ROI rank:** 3 (pre-1.0)
- **Depends on:** none. SPEC-26 (untruncated rendering) should land after or together with this spec — they share one `RENDER_BODY_VERSION` bump.

## Why

Reviewers emit `suggestion` and `evidence` (requested by `prompts/review.md`,
validated by `finding_batch.schema.json`), and `render.py` contains rendering
support for both — but the consensus group schema
(`consensus.schema.json` group `$defs`, `additionalProperties:false`) has no
such keys, so `build_consensus` cannot legally carry them and the render
branches are dead. Posted comments never show suggestions or evidence detail.

Similarly, critique **dispute rationales** are collected and validated
(`critique_batch.schema.json:rationale`) but only counters survive into
groups. Maintainer decisions:

1. propagate the representative finding's suggestion/evidence into the group;
2. per-reviewer evidence is repetitive when reviewers **agree** — de-emphasize
   it (it mostly restates the finding);
3. dispute rationales are valuable — **surface them** in the posted body.

## Scope

**In:** consensus group construction, consensus schema, `types.py`, render
layout, golden fixtures, CHANGELOG migration note.

**Out:** truncation policy (SPEC-26); critique `noise` rationales (dropped
groups aren't posted); any change to voting/severity policy — dispute
rationale is display data only.

## Implementation

1. `ai-review/src/ai_review/consensus.py` — group construction in
   `build_consensus`:
   - `suggestion`: the representative finding's `suggestion` (string|null).
   - `evidence_by_reviewer`: for each contributing reviewer with non-empty
     evidence, `{reviewer: "; ".join(evidence)}`; if a reviewer contributed
     multiple findings to the group, concatenate in `source_finding_id`
     order.
   - `critique_disputes`: new list populated in `_apply_critiques` from
     selected critiques whose **effective** verdict is `dispute` (including
     invalid-duplicate demotions):
     `{"critic": <reviewer name>, "rationale": str,
     "adjusted_severity": str|null}`, sorted by (critic, rationale) for
     determinism. Populated regardless of `allow_severity_downgrade` —
     severity policy is unchanged. Self-critiques remain excluded (existing
     contributing-reviewer filter).
2. `ai-review/schemas/consensus.schema.json` group `$defs`: add **optional**
   properties `suggestion` (`["string","null"]`), `evidence_by_reviewer`
   (object with string values), `critique_disputes` (array of objects:
   required `critic`, `rationale`; optional `adjusted_severity`
   string|null). Not added to `required` so external consumers of older
   consensus.json artifacts keep validating; new groups always include them.
3. `ai-review/src/ai_review/types.py`: `FindingGroup` already declares
   `suggestion`/`evidence_by_reviewer`; add
   `critique_disputes: NotRequired[list[CritiqueDispute]]` plus the
   `CritiqueDispute` TypedDict. Update `test_types_schema_alignment.py`.
4. `ai-review/src/ai_review/render.py` — `render_body`:
   - Evidence: render the representative body once. Render
     `evidence_by_reviewer` bullets **only** for entries materially distinct
     from the group body/title (skip entries whose `normalize_text(...)`
     equals the normalized body or title — agreement adds no signal). When
     nothing distinct remains, omit the Evidence section entirely.
   - New **Dissent** section when `critique_disputes` is non-empty:
     `- <critic> disputes: <rationale>` (append
     `(suggested severity: X)` when `adjusted_severity` is set). Renders for
     surfaced and blocking findings alike.
   - Suggestion block: the existing path becomes live; keep
     `validate_suggestion` gating (fence balance, no HTML-comment markers).
   - Bump `RENDER_BODY_VERSION` to `render-body.v2` (shared with SPEC-26 if
     landed together) and add the CHANGELOG migration note: existing
     bot-authored threads receive a one-time update on the next run
     (precedent: 0.2.0 body-hash change).
5. Golden fixtures: `make update-golden`; review the diff — new fields must
   appear, decisions/votes must not change.

## Acceptance criteria

- A run where reviewer A surfaces a finding and reviewer B disputes it with a
  rationale produces a posted body containing a Dissent section with B's
  rationale.
- Reviewer suggestions appear in posted bodies (when they pass
  `validate_suggestion`).
- Agreeing reviewers no longer produce N near-identical evidence bullets.
- consensus.json validates against the updated schema; decisions, votes,
  severities, and `block_merge` are byte-identical to before except the new
  fields and body hashes.

## Tests

- Consensus: new fields populated correctly (multi-finding reviewer
  concatenation; dispute from a non-contributing critic recorded;
  self-critique still excluded; invalid duplicate demoted to dispute appears
  in `critique_disputes`); determinism (two runs, same bytes).
- Render: dissent section on/off; duplicate-evidence suppression; distinct
  evidence kept; suggestion rendering with valid/invalid fences.
- Schema round-trip via `validate_instance`; golden snapshot refresh.

## Risk / rollback

Schema additions are optional-key only — no consumer break. Body changes
cause a one-time thread update wave (documented). Rollback = revert;
`RENDER_BODY_VERSION` returns to v1 and threads update once more.
