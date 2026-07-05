# Phase 5 Acceptance Evidence

## Local Validation

- Status: complete on 2026-07-04 after live-provider hardening
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest discover -s ai-review/tests -p 'test_*.py'`: passed, 184 tests
- `PYTHONPYCACHEPREFIX=/tmp/ai-review-compile-downstream-phase5-fix2 python3 -m compileall -q ai-review/src`: passed
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
- Pipeline URL: https://gitlab.example.internal/example-org/downstream-app/-/pipelines/179454
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

## Final Configuration Decision: Critique Enabled by Default

- Status: accepted
- Decision reversal: the critique phase shipped in the trusted config as **permanently enabled**, not disabled-by-default as originally planned above. Commit `f7f1490` ("enable critique") flipped `critique.enabled: false → true`, `rounds: 0 → 1`, `allow_advisory_escalation: false → true`, and `allow_severity_downgrade: false → true` in [config/review.yaml](config/review.yaml).
- This is the config now documented in the root [README.md](../README.md) configuration reference.

## Live Verification of the Enabled-by-Default Config

- Status: accepted
- Pipeline: `179684`
- Pipeline URL: https://gitlab.example.internal/example-org/downstream-app/-/pipelines/179684
- Pipeline source/ref: `merge_request_event`, `refs/merge-requests/3144/head`
- MR: `!3144`, "Revert \"remove fake bad code\""
- Source branch: `ai-review-smoke-throw-away`
- Head SHA: `53f832b0c2ccb201ba2f529247060f1a14c49517`
- Run ID: `gl-179684-2529360`
- Trusted image SHA (pinned in `ci/review.gitlab-ci.yml` at run time): `3c484052e41cbe99b45339f4f4afccf72538e5b7`
- Pipeline status: failed as expected because `ai_review_gate` enforced blocking consensus findings on the permanently-enabled critique config.
- Full stage-by-stage breakdown of this run, including the consensus critique-summary self-exclusion rule it revealed, is documented in [EXAMPLE_PIPELINE_WALKTHROUGH.md](EXAMPLE_PIPELINE_WALKTHROUGH.md).

AI-review jobs:

- `prepare_ai_review`: job `2529360`, success.
- `review_claude`: job `2529361`, success; 2 findings.
- `review_codex`: job `2529362`, success; 2 findings.
- `review_opencode`: job `2529363`, success; 2 findings.
- `critique_claude`: job `2529364`, success; `adapter_status=success`, critiques=6.
- `critique_codex`: job `2529365`, success; `adapter_status=success`, critiques=5.
- `critique_opencode`: job `2529366`, success; `adapter_status=success`, critiques=6.
- `consensus_ai_review`: job `2529367`, success; `panel_status=full`, `successful_reviewers=[claude,codex,opencode]`, `failed_reviewers=[]`, 4 groups (`surface_count=4`, `fyi_count=0`, `drop_count=0`), `block_merge=true`.
- `post_ai_review`: job `2529368`, success; `created_discussions=4`, `updated_discussions=0`, `skipped_unchanged=0`, `warnings=[]`.
- `ai_review_gate`: job `2529369`, failed as expected with `status=failed_blocking_findings`, `reason=blocking_consensus`, `block_merge=true`.

Artifact checks:

- All `out/findings/{claude,codex,opencode}.json` artifacts validate against `finding_batch.schema.json`.
- All `out/critiques/{claude,codex,opencode}.json` artifacts validate against `critique_batch.schema.json`; top-level `critic` and per-critique `critic` match the filename.
- All critique jobs wrote `out/pooled_findings/{critic}.json` with blinded labels `reviewer_A`/`reviewer_B`/`reviewer_C`, preserved `src/foo.py` anchors, and preserved finding evidence/body text verbatim.
- Consensus validates against `consensus.schema.json`, has `panel_status=full`, and contains 4 groups: the single 3-vote SQL-injection group (`blocker`, `block_merge=true`) plus 3 single-reviewer `minor` groups, all `surface`d.
- Critique verdicts on the 3-vote SQL-injection group all evaluate to zero (`agree=0, duplicate=0, noise=0`) because every successful critic was already a contributing reviewer on that group; `_apply_critiques` in `consensus.py` excludes a critic's verdict from a group's summary when that critic is one of the group's own contributing reviewers. The 3 single-reviewer groups each show non-zero critique support from the 2 reviewers who did not originally find that issue.
- `allow_advisory_escalation` and `allow_severity_downgrade` were both live (`true`) for this run; no group exercised an escalation or downgrade path — the one `noise` verdict on the unused-variable group did not cross the `> len(eligible_critics)/2` drop threshold (1 noise vote of 2 eligible critics).

## Secret Audit

- Status: complete for pipeline `179454` and pipeline `179684`
- Scope (`179454`): traces and downloaded artifacts for jobs `2528806` through `2528815`.
- Scope (`179684`): traces and downloaded artifacts for jobs `2529360` through `2529369`.
- No provider API keys, GitLab read/write tokens, Jira tokens, CLI auth files, CLI session files, or shell history files were found in runtime AI-review traces or generated AI-review outputs for either pipeline.
- Trace matches were limited to GitLab runner coordinator snippets such as `token=glcbt-64`, literal command text such as `echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa`, and Codex session IDs.
- Value-like matches outside runtime AI-review outputs were limited to committed repo-snapshot files, including Firebase app config keys and redaction-test fixtures.
