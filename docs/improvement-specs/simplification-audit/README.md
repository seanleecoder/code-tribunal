# Code Tribunal simplification audit

A static review audited main at `ec7e82754db062797a9c4d0646ced79ac2d8e7ff`
(3 October 2026), and a second pass on 2026-10-14 added items N01–N20. The
completed items are visible in the code, tests and
[`CHANGELOG.md`](../../../CHANGELOG.md). The review text was deleted on
2026-11-15 and remains in git history
(`docs/improvement-specs/simplification-audit/SIMPLIFICATION_REVIEW.md` at
`ef8a13b`), with the full disposition tables in this file at the same commit.

The rows below are what remains open. They are evidence for future decisions,
not implementation instructions: each item still needs its own decision, and an
item that adds a product dimension needs an accepted ADR first.

## Open items

Status reviewed against main at `ef8a13b` on 2026-11-15.

| ID | Item | Status | Next step |
|---|---|---|---|
| S08 | Explicit pinned transport codecs | Partial: per-seat codecs and Cursor's final-answer text transport are done. Fallback deletion is deferred. | Current pinned-CLI traces must prove a fallback obsolete before deletion; see [S08 evidence](#s08-evidence). |
| S13 | Remove absence-based thread resolution | **Kept for now (2026-10-13).** | Revisit with run-health data on degraded-run frequency. When revisited, consider "auto-resolve only on a full-grade run" as a replacement for `min_successful_reviewers_for_resolution`, not an addition (noted 2026-10-16). |
| S18 | Prune tests by contract ownership | Partial: `test-strict` and `compile` targets and retired-machinery tests removed. | Continue alongside each owning change; no standalone work. |
| S19 | Reduce policy/docs; freeze speculative expansion | Partial: contributor docs updated; SPEC-46 archived and SPEC-47's repository-hosted policy rejected on this basis (2026-10-13); completed audit and index material pruned (2026-11-15). | No new product expansion is authorized. |
| X01 | Group once before critique | Not started | Optional experiment; not on the roadmap. |

Done or closed: S01–S07, S09–S12, S14–S17 and N01–N20. S07 closed as a bounded
cleanup on 2026-11-01: the remaining Claude, Codex and Cursor shells only check
prerequisites and invoke the CLI with Python-built arguments, and no further
launcher rewrite is planned.

**Rejected or deferred product calls:**

- **Rejected:**
  - structured output only for every seat, because it drops Cursor and needs an
    ADR;
  - state stored only in threads, because it needs an ADR and loses FYI identity;
  - merging the review and critique prompts;
  - dropping per-seat effort settings;
  - one canary covering both platforms.
- **Deferred until run-health data exists:**
  - making Codex structured-output only.
- **Overtaken (closed 2026-11-27):**
  - Claude's JSON envelope versus stream-json: stream-json now also carries the
    repository-tool health correlation in `adapter_output.py` and the canary
    read witness in `canary_repo_read.py`, so the envelope would drop required
    evidence, not only the timeout trace;
  - making OpenCode structured-output only: `@opencode/cli` v2 has no
    generation-time schema enforcement (see
    [configuration](../../configuration.md#reviewers)).

## S08 evidence

S08's remaining parser rules have documented observed failure cases in
`adapter_output.py`, `test_adapter_runner.py` and `test_opencode_client.py`.
The checked-in OpenCode answer parts are replayed through the current HTTP
transport, but they are not a complete captured corpus for the current pins in
`ai-review/images/package.json`. Keep the Claude/OpenCode text fallback and
stringified payload handling. Removing them requires a separately reviewed
captured corpus, with duplicate-key and ambiguity rejection preserved.

## Standing notes from completed items

- **S10 ordering guarantee.** Output ordering is preserved for normalizer
  output, whose source IDs bind normalized path and category. A hand-built
  artifact that reuses one source ID across path/category buckets may order tied
  groups differently.
- **S17 scope membership.** Private case names and per-scope counts are not
  distribution contracts. Every case module must still belong to exactly one
  smoke scope.
