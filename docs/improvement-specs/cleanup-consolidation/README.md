# SPEC-60 handoff — critique-quality observability

SPEC-54 through SPEC-59 are implemented. SPEC-61's dual-platform candidate
canary is implemented by the protected manual workflow and release procedure.
SPEC-60 is the only remaining cleanup/consolidation follow-up.

## Objective

Expose enough aggregate critique-quality information to tell whether critique is
useful without changing any reviewer, reducer, posting, or merge-policy outcome.

## Constraints

- Observational only: no new decision input, quorum, gate, retry policy, or model
  stage.
- Preserve the four first-party reviewers and the single review/single critique
  pipeline on both platforms.
- Do not retain model bodies, prompts, credentials, or provider session data.
- Prefer existing status/consensus artifacts; a public schema change requires an
  explicit version decision.
- A missing metric must never change `post` exit status or restore a merge gate.

## Work still required

Write a decision-complete SPEC-60 that defines the exact aggregate fields,
redaction rules, retention destination, schema/version impact, tests, and deletion
condition. Separate this observational work from ordinary reducer-policy changes.
