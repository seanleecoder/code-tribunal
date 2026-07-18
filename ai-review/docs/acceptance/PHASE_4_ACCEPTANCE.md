# Phase 4 Acceptance

> **Historical evidence.** This record predates the current 1.0 evidence matrix
> and is non-normative; see the [evidence index](../../../docs/history/evidence/README.md).

Status: Phase 4 implementation synced; baseline real MR smoke passed; anchor-drift smoke passed conservative safety checks with missing-remap fallback.

Date: 2026-07-03
Branch: `ai-review-poc-throw-away`

## Model Availability

- Target OpenCode model: `google/gemini-3.1-flash-lite`
- Public OpenRouter `/api/v1/models` check: exact id present.
- Authenticated OpenRouter check: not run from this local shell because `OPENROUTER_API_KEY` is not set here.

## Local Validation

- Unit tests: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest discover -s ai-review/tests -p 'test_*.py'`
  - Result: pass, 155 tests.
- Compile check: `PYTHONPYCACHEPREFIX=/tmp/ai-review-compile-downstream python3 -m compileall -q ai-review/src`
  - Result: pass.
- Active model grep: `rg "gemini-3\\.5-flash" ai-review`
  - Result: only historical Phase 2 acceptance evidence remains.
- Whitespace: `git diff --check`
  - Result: pass.

## Implementation Notes

- Phase 4 state-note persistence, state aliases, conservative post planning, and anchor remapping code/tests were synced into this checkout.
- Downstream CI branch rules for `ai-review-*` and the existing trusted image SHA were preserved during sync.
- Downstream Claude reviewer config remains `anthropic/claude-haiku-4.5`.
- OpenCode active config/tests/docs now use `google/gemini-3.1-flash-lite`.
- Trusted image observed in drift gate trace: `registry.example.internal/example-org/downstream-app:ai_review_base_1_1_f4723dd31834d53c4ba4395ea8fb1cdd54e3f913`.

## Baseline MR Smoke

- Pipeline: `179295`
- MR: `!3137`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`
- Head SHA: `9fb44ee0e3d08f29c2a2a7606746e90735190223`
- Run id: `gl-179295-2527701`
- Pipeline status: failed as expected because `ai_review_gate` blocked on consensus findings.

AI-review jobs:

- `prepare_ai_review`: job `2527701`, success.
- `review_claude`: job `2527702`, success.
- `review_codex`: job `2527703`, success.
- `review_opencode`: job `2527704`, success.
- `consensus_ai_review`: job `2527705`, success.
- `post_ai_review`: job `2527706`, success.
- `post_ai_review` retry: job `2527786`, success.
- `ai_review_gate`: job `2527707`, failed with expected blocking result.
- `ai_review_gate` retry: job `2527787`, failed with same expected blocking result.

Artifact checks:

- `prepare_ai_review` artifact includes `inputs/state_aliases.json` with `schema_version: state_aliases.v1` and no prior records.
- `review_opencode` artifact validates against schemas and reports `adapter_status=success`, `model=google/gemini-3.1-flash-lite`.
- `consensus_ai_review` artifact validates against schemas and reports `panel_status=full`, `successful_reviewers=[claude,codex,opencode]`, `failed_reviewers=[]`, and 3 surfaced groups.
- First `post_ai_review` artifact validates against schema and reports `created_discussions=3`, `updated_discussions=0`, `warnings=[]`.
- Retried `post_ai_review` artifact validates against schema and reports `created_discussions=0`, `skipped_unchanged=3`, `warnings=[]`.
- Both `ai_review_gate` artifacts validate against schema and report `block_merge=true`, `reason=blocking_consensus`, `status=failed_blocking_findings`.

MR discussion checks:

- AI-review inline discussions: 3, matching the first post result.
- Duplicate AI-review discussions after post retry: none observed.
- State note count: 1.
- State note visible text: `AI review state. Machine-owned; do not edit.`
- State note marker: exactly one `ai-review-state:v1` marker with base64url payload and `state_hash=...`.
- State payload hash: marker hash matches SHA-256 of the decoded payload.
- State payload validates against `state.schema.json`.
- State records: 3 open records, each linked to a posted discussion/root note, all with `remap_status=exact`.

## Anchor-Drift Smoke

- Pipeline: `179304`
- MR: `!3137`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`
- Head SHA: `67273651a5de2c2c100b2d83d6ad991a435b8dd3`
- Run id: `gl-179304-2527796`
- Pipeline status: failed as expected because `ai_review_gate` blocked on consensus findings.
- Drift change: inserted harmless `ANCHOR_DRIFT_NOTE`, `ANCHOR_DRIFT_PADDING`, and `anchor_drift_prelude()` lines before the existing `multi_issue_probe` smoke line; the vulnerable line remained intact.

AI-review jobs:

- `prepare_ai_review`: job `2527796`, success.
- `review_claude`: job `2527797`, success.
- `review_codex`: job `2527798`, success.
- `review_opencode`: job `2527799`, success.
- `consensus_ai_review`: job `2527800`, success.
- `post_ai_review`: job `2527801`, success.
- `ai_review_gate`: job `2527802`, failed with expected blocking result.

Artifact checks:

- `prepare_ai_review` artifact includes `inputs/state_aliases.json` with 3 prior records loaded from the baseline state note.
- `review_opencode` artifact validates against schemas and reports `adapter_status=success`, `model=google/gemini-3.1-flash-lite`.
- `consensus_ai_review` artifact validates against schemas and reports `panel_status=full`, `successful_reviewers=[claude,codex,opencode]`, `failed_reviewers=[]`, and 3 surfaced groups.
- All 3 consensus groups report `issue_id_source=matched_state` with `state_match.status=matched` and `state_match.precedence=symbol_title`.
- `post_ai_review` artifact validates against schema and reports `created_discussions=0`, `updated_discussions=0`, `resolved_discussions=0`, `stale_unverified=3`, `summary_comment.action=created`, `summary_comment.surface_findings=3`.
- `post_ai_review` warnings explicitly report missing remap for all 3 prior issue IDs and summary fallback posting.
- `ai_review_gate` artifact validates against schema and reports `block_merge=true`, `reason=blocking_consensus`, `status=failed_blocking_findings`.

Drift MR discussion checks:

- AI-review inline discussions after drift: still 3 baseline inline discussions.
- Duplicate AI-review inline discussions after drift: none observed.
- Summary fallback comments after drift: 1 created for the 3 unposted inline findings.
- State note count after drift: 1.
- State note visible text and marker shape remain valid.
- State payload hash matches the marker hash and validates against `state.schema.json`.
- Drift state records: all 3 prior records are preserved with original discussion/root note links, `status=stale_unverified`, `remap_status=missing`, and `last_seen_sha=67273651a5de2c2c100b2d83d6ad991a435b8dd3`.
- No corrupt state warning observed.
- No stale, ambiguous, protected, or missing-remap record was auto-resolved.

Drift conclusion:

- Positive remap was inconclusive: no live record reached `remap_status=remapped`.
- Conservative behavior passed: explicit missing-remap warnings, no duplicate inline discussions, no corrupt state, and no unsafe auto-resolution.

## Secret Audit

Baseline traces downloaded and scanned for jobs `2527701`, `2527702`, `2527703`, `2527704`, `2527705`, `2527706`, `2527786`, `2527707`, and `2527787`.

Drift traces downloaded and scanned for jobs `2527796`, `2527797`, `2527798`, `2527799`, `2527800`, `2527801`, and `2527802`.

Results:

- No provider keys, usable GitLab tokens, CLI auth/session files, or shell history files were found in runtime traces or generated AI-review outputs.
- Scan matches were limited to source-tree example/test literals inside `inputs/repo_snapshot/ai-review`, not runtime secrets.
