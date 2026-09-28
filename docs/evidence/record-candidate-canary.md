# Evidence record: GitHub + GitLab / Candidate Canary real four-seat panel / 2026-09-27

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

The release's real-panel (Chain A) row on **both** platforms. The canary retains
only redacted `candidate_canary_summary.v1` artifacts; this record is written by
the operator from them.

## Identity

- Workflow: `Candidate Canary` (`.github/workflows/candidate-canary.yml`), run
  `36313164907`, dispatched once from protected `main` on 2026-09-27 and approved in
  the `candidate-canary` environment
- Runtime source `R`: `71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f`
- Base: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02`
- Reviewer: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c`
- GitHub: `seanleecoder/code-tribunal-demo` PR #23, review run `36313723237`
  (`pull_request`)
- GitLab: `seanleecoder/code-tribunal-demo` (project `84667714`) MR !21, pipeline
  `2886794616`
- Artifacts: `candidate-canary-github-summary`, `candidate-canary-gitlab-summary`

## Preconditions

- Four seats — Claude, Codex, OpenCode, Cursor — with shipped default effort; one
  review and one critique per seat.
- Models as resolved at the provider: Claude `anthropic/claude-haiku-4.5`, Codex
  `openai/gpt-5.6-luna`, OpenCode `google/gemini-3.5-flash-lite`, Cursor
  `composer-2.5`. Cursor ran the demo consumers' pinned slug, not the shipped
  `auto` selector.
- GitHub demo `AI_REVIEW_MANUAL=false`, so the pull request's own run was the
  campaign run and the canary dispatched nothing.

## Image identity (`verify-candidate`)

| Check | base | reviewer |
|---|---|---|
| Digest | `sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02` | `sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c` |
| Source reachable from protected `main` | yes | yes |
| `org.opencontainers.image.revision` | `= R` | `= R` |
| Provenance attestation (publish workflow, `refs/heads/main`, `R`) | verified | verified |

Publication run `36312633309`; quality run `36312633166`.

## Actual result

| | GitHub | GitLab |
|---|---|---|
| Stage results (4 review + 4 critique) | 8/8 success | 8/8 success |
| `panel_status` | `full` | `full` |
| Resolution-eligible reviewers | claude, codex, cursor, opencode | claude, codex, cursor, opencode |
| Posting status | `success` | `success` |
| Posted threads | 5 | 1 |
| Cleanup | `success` | `success` |

Exactly one AI Review run executed on the GitHub demo for this campaign.

## Audit

- Artifacts inspected: both summary JSON files. They carry no model bodies by
  construction.
- `python scripts/scan_evidence_leaks.py` over both summary artifacts: 2 files,
  10 pattern/entropy detectors, no credential material. An exact-value scan was
  not performed.
- **Known unexercised paths:** the shipped three-seat default roster and Cursor
  `auto` were not the configuration exercised; nothing here verifies Cursor's
  deny policy at runtime; lifecycle, refresh, and hostile-MR rows are separate
  records.

## Superseded attempt

The first 2.0.0 candidate
(`R = d42559aa55f1bd9396d0a157f1c8268e6743e709`, Candidate Canary run
`36113026891`) failed: on GitHub a critic miscopied a pooled finding id and
consensus exited 3. PR #131 fixed that at critique finalization, and PR #132
removed a duplicate GitHub panel run the same attempt exposed. That attempt is
retained as failed validation and certifies nothing.

## Verdict

Scoped pass. Against the frozen `R` and final image pair, one real four-seat
review-and-critique panel completed on each platform with every seat successful
and resolution-eligible, a full panel, successful posting, and clean teardown.
