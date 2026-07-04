# Phase 5 Acceptance Evidence

## Local Validation

- Status: complete on 2026-07-04 after live-provider hardening
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest discover -s ai-review/tests -p 'test_*.py'`: passed, 184 tests
- `PYTHONPYCACHEPREFIX=/tmp/ai-review-compile-burda-phase5-fix2 python3 -m compileall -q ai-review/src`: passed
- `git diff --check`: passed
- Stale-model grep over `ai-review/src`, `ai-review/adapters`, `ai-review/config`, `ai-review/ci`, `ai-review/tests`, and `ai-review/README.md`: no stale Claude/Gemini model references; only current `google/gemini-3.1-flash-lite` references. `antigravity` appears only in the negative assertion test that prevents legacy job names from returning.
- Code Tribunal sync validation: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest discover -s ai-review/tests -p 'test_*.py'` passed, 183 tests; `PYTHONPYCACHEPREFIX=/tmp/ai-review-compile-code-tribunal-phase5-sync python3 -m compileall -q ai-review/src` passed; `git diff --check` passed.

## Temporary Critique-Enabled Image

- Status: complete
- Temporary trusted config: `critique.enabled=true`, `critique.rounds=1`, `allow_advisory_escalation=false`, `allow_severity_downgrade=false`
- Temporary trusted image SHA: `cf54cae8f6e99622452d7bf0669f8dfb1fdeb1f4`
- Pinned by `ai-review/ci/review.gitlab-ci.yml` in commit `9c24f265c` for the live smoke.

## Live MR Smoke

- Status: accepted
- Pipeline: `179454`
- Pipeline URL: https://gitlab.burdaverlag.dev/burda_style/head/-/pipelines/179454
- Pipeline source/ref: `merge_request_event`, `refs/merge-requests/3142/head`
- MR: `!3142`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`
- Head SHA: `d1dbd1873403128ccd651273339817a7194ba5bf`
- Run ID: `gl-179454-2528806`
- Pipeline status: failed as expected because `ai_review_gate` enforced blocking consensus findings.

AI-review jobs:

- `prepare_ai_review`: job `2528806`, success.
- `review_claude`: job `2528807`, success.
- `review_codex`: job `2528808`, success.
- `review_opencode`: job `2528809`, success.
- `critique_claude`: job `2528810`, success; `adapter_status=success`, critiques=5.
- `critique_codex`: job `2528811`, success; `adapter_status=success`, critiques=5.
- `critique_opencode`: job `2528812`, success; `adapter_status=success`, critiques=5.
- `consensus_ai_review`: job `2528813`, success; `panel_status=full`, `successful_reviewers=[claude,codex,opencode]`, `failed_reviewers=[]`.
- `post_ai_review`: job `2528814`, success; `created_discussions=2`, `updated_discussions=0`, `warnings=[]`.
- `ai_review_gate`: job `2528815`, failed as expected with `status=failed_blocking_findings`, `reason=blocking_consensus`, `block_merge=true`.

Artifact checks:

- All `out/findings/{claude,codex,opencode}.json` artifacts validate against `finding_batch.schema.json`.
- All `out/critiques/{claude,codex,opencode}.json` artifacts validate against `critique_batch.schema.json`; top-level `critic` and per-critique `critic` match the filename.
- All critique jobs wrote `out/pooled_findings/{critic}.json`; each pool used blinded labels `reviewer_A`/`reviewer_B`, preserved `src/foo.py`, and preserved finding evidence/body text.
- Consensus validates against `consensus.schema.json`, has `panel_status=full`, and contains 3 groups: 2 surfaced and 1 FYI.
- Consensus groups contain `critique_summary`; each surfaced group has `vote_count=2`, based on contributing finding authors, not critique votes.
- All three critics returned duplicate verdicts: `{agree: 3, duplicate: 2}`.

## Final Disabled-Default Image

- Status: pending
- Final trusted config: `critique.enabled=false`, `critique.rounds=0`
- Final trusted image SHA: pending

## Secret Audit

- Status: complete for pipeline `179454`
- Scope: traces and downloaded artifacts for jobs `2528806` through `2528815`.
- No provider API keys, GitLab read/write tokens, Jira tokens, CLI auth files, CLI session files, or shell history files were found in runtime AI-review traces or generated AI-review outputs.
- Trace matches were limited to GitLab runner coordinator snippets such as `token=glcbt-64`, literal command text such as `echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa`, and Codex session IDs.
- Value-like matches outside runtime AI-review outputs were limited to committed repo-snapshot files, including Firebase app config keys and redaction-test fixtures.
