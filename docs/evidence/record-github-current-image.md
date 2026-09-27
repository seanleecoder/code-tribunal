# Evidence record: GitHub / current-image lifecycle (Chain B, adding fixture) / 2026-09-27

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Chain B of the 2.0.0 campaign: deterministic mock reviewer, zero tokens, driving
the real GitHub posting, state, and command APIs on one mock finding identity.
There is no gate: step 8 checks that a `blocker` finding leaves the change
mergeable.

## Identity

- Platform: GitHub Actions (github.com), container jobs, same-repository pull request
- Date: 2026-09-27
- Consumer project: `seanleecoder/code-tribunal-demo`
  (see [consumer projects](CONSUMER-PROJECTS.md))
- Change request: PR #25, branch `evidence/chain-b-71dfabc`, based on the adoption
  branch of PR #24 (merged as `b44ab38`), so every run used the candidate workflow
- Workflow runs: `36315450618` attempts 1–10 on head `e96e155`, then `36342268656`
  on head `babf6fc`
- Source commit: `71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f`
- Base image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:f7028a5a…`
- Reviewer image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:292142b7…`

## Preconditions

- Mock enabled through repository variables only: `AI_REVIEW_LOCAL_MOCK=1`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, every `AI_REVIEW_REQUIRE_REAL_*=0`, and
  `AI_REVIEW_MOCK_SCENARIO` flipped between attempts. Four seats on the roster.
- No required status check on `main`. The classic branch protection still
  required the retired `gate` check at the start of this campaign and was removed
  by the operator, per the 2.0 upgrade guide.
- Fixture adds `src/audit.py` and modifies `src/access.py` (the PR #10 fixture).
  GitHub renders the added file with `--- /dev/null`.
- All mock variables were deleted after the chain and confirmed absent.

## Actual result

| Step | Scenario / action | Run (attempt) | `post_result` | Platform observation |
|---|---|---|---|---|
| 1 create | `blocking` | `36315450618` (1) | `created_discussions=1` | inline comment `4115145151` on **`src/audit.py:6`**, the added file |
| 2 unchanged | `blocking` rerun | (2) | `skipped_unchanged=1`, `created=0` | no duplicate |
| 3 changed body | `blocking_alt` | (3) | `updated_discussions=1` | same comment rewritten in place; `body_hash` `5516b904…` → `48b7ad1a…`; live body renders the `render-body.v4` `Support:` footer |
| 4 wontfix | reply `/ai-review wontfix` (`4115161981`), `blocking` | (7) | `resolved_discussions=1` | thread resolved; state record `status: wontfix` |
| 4 persists | `blocking` rerun | (8) | `skipped_unchanged=1`, `resolved=0` | still resolved; state still `wontfix` |
| 5 reopen | reply `/ai-review reopen` (`4116510106`), `blocking` | (9) | `created=0` | thread unresolved; state `open`; same `issue_id` `761fd1ba…` |
| 7 stale head | push `babf6fc` while reviews ran | (10) | `status: stale_head`, all counts 0 | run concluded `success`; nothing written |
| 8 blocker does not block | `blocking` on the new head | `36342268656` (1) | `status: success`, `skipped_unchanged=1` | `**AI review: BLOCKER correctness**` thread present; run green; PR `MERGEABLE` / `CLEAN` |

Step 1, every seat: raw 1, accepted 1 (claude, codex, cursor, opencode). The
added-file path holds on the candidate.

Step 6 (unrelated line movement) is regression-covered and was not run live, as
the runbook allows.

### Step 4 needed a credential fix

Attempt 4 posted `/ai-review wontfix`, but resolving the thread failed with
`failed to resolve thread 4115145151: GitHub API POST …/graphql failed: 401`.
The thread stayed open and the state record stayed `open`. `post` still
reported `status: success` with that warning. The consumer's
`AI_REVIEW_GITHUB_RESOLVE_TOKEN` (set 2026-07-21) had been rejected. The
operator rotated it; attempts 5–6 were the operator's own reruns while doing
so. Attempt 7 reprocessed the unchanged command and resolved the thread.

## Audit

- Artifacts inspected: `ai-review-post` for every step, all four
  `ai-review-review-*` from step 1, and the posted comment bodies and review-thread
  state through the REST and GraphQL APIs.
- `python scripts/scan_evidence_leaks.py` over the downloaded CI artifacts: 19
  files, 10 detectors, no hits. The decoded PR state note was inspected separately
  and is not an artifact.
- **Known unexercised paths:** platform-visible re-anchoring on line movement;
  the below-quorum FYI and inline-unmappable fallbacks (regression-covered).
- **Observation:** a failed thread resolution is only a `post_result` warning,
  so an operator whose resolve token expires sees green runs while `wontfix`
  never sticks.

## Verdict

Scoped pass. On the frozen `R` and final image pair, one mock finding identity
on an added file went through create, unchanged rerun, in-place body change,
`wontfix` with persistence, reopen, and a stale-head no-op. A `blocker`
thread left the change mergeable.
