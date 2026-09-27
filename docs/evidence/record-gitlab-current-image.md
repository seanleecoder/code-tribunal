# Evidence record: GitLab / current-image lifecycle (Chain B, adding fixture) / 2026-09-27

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Chain B of the 2.0.0 campaign on the second, independent render surface:
deterministic mock reviewer, zero tokens, driving the real GitLab discussion,
state, and command APIs on one mock finding identity. There is no gate.

## Identity

- Platform: GitLab.com, hardened child topology
- Date: 2026-09-27, ~19:03–19:16 UTC
- Consumer project: `seanleecoder/code-tribunal-demo` (project `84667714`)
- Template: `seanleecoder/code-tribunal-ci-template` at
  `f8ae2138261d03447f68e5a30ff9ac44ac202bcb`. Both templates are byte-identical
  to code-tribunal `main` `f686b30`. The consumer's two includes point at that
  SHA (consumer commit `5900e34`).
  `scripts/pipeline_trust.py --mode child` accepted the composition.
- Change request: MR !22, protected source branch `evidence/chain-b-71dfabc`
  (fixture commit `a175643`), closed afterwards
- Child pipelines: `2887346562`, `2887350332`, `2887355969`, `2887360151`,
  `2887362236`, `2887363945`
- Source commit: `71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f`
- Base image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:f7028a5a…`
- Reviewer image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:292142b7…`

## Preconditions

- Child topology: one `ai_review` trigger job, `inherit.variables: false`,
  `strategy: mirror`, both forward flags false, two same-project includes at one
  SHA.
- Source branch protected before the MR was opened, so the protected
  `GITLAB_TOKEN` injected.
- Mock enabled as **project** CI/CD variables: `AI_REVIEW_LOCAL_MOCK=1`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, every `AI_REVIEW_REQUIRE_REAL_*=0`,
  `AI_REVIEW_MOCK_SCENARIO` edited in place between steps. All were deleted
  afterwards and confirmed absent.
- Each step re-driven with `POST /projects/84667714/merge_requests/22/pipelines`.
- Fixture **adds** `src/audit.py` (the MR !13 fixture).

## Actual result

| Step | Scenario / action | Child pipeline | `post_result` | Platform observation |
|---|---|---|---|---|
| 1 create | `blocking` | `2887346562` | `created_discussions=1` | inline note `3913115354` on **`src/audit.py:6`**, the added file, plus a summary note |
| 2 unchanged | `blocking` | `2887350332` | `skipped_unchanged=1`, `created=0` | no duplicate |
| 3 changed body | `blocking_alt` | `2887355969` | `updated_discussions=1` | same note rewritten in place; `body_hash` `5516b904…` → `48b7ad1a…`; `render-body.v4` `Support:` footer |
| 4 wontfix | reply `/ai-review wontfix` (`3913128009`), `blocking` | `2887360151` | `resolved_discussions=1` | discussion resolved; bot state note `3913132532` written |
| 4 persists | `blocking` | `2887362236` | `skipped_unchanged=1`, `resolved=0` | still resolved |
| 5 reopen | reply `/ai-review reopen` (`3913138656`), `blocking` | `2887363945` | `created=0` | discussion unresolved; same note, no new discussion |
| blocker does not block | after step 5 | — | `status: success` | `detailed_merge_status: mergeable`, `has_conflicts: false`, with the `blocker` thread open |

Step 1, every seat: raw 1, accepted 1 (claude, codex, cursor, opencode). This
is the first live GitLab run with a finding on an added file, and it closes the
carried coverage gap.

All child pipelines concluded `success`. Every job in each child succeeded.

### Cross-platform determinism

The `body_hash` values match the GitHub Chain B of this campaign
([record](record-github-current-image.md)) exactly, `5516b904…` for `blocking` and
`48b7ad1a…` for `blocking_alt`. As at 1.0.1, rendering is platform-independent.

## Audit

- Artifacts inspected: `post_ai_review` artifacts from every child pipeline and the
  four review artifacts of step 1; discussion and merge-request state through the
  GitLab API.
- `python scripts/scan_evidence_leaks.py` over the downloaded post artifacts: 6
  files, 10 detectors, no hits.
- **Known unexercised paths:** stale-head no-op (GitHub-only in this campaign);
  refresh of the 1.0.0-format GitLab note `3601861614` on MR !11; platform-visible
  re-anchoring on line movement.

## Verdict

Scoped pass. On the frozen `R` and final image pair, in the hardened child
topology, one mock finding identity on an added file went through create,
unchanged rerun, in-place body change, `wontfix` with persistence, and reopen.
A `blocker` thread left the merge request mergeable.
