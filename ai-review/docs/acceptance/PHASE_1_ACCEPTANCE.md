# Phase 1 Acceptance

> **Historical evidence.** This record predates the current 1.0 evidence matrix
> and is non-normative; see the [evidence index](../../../docs/history/evidence/README.md).

This file records the private GitLab MR smoke evidence for Phase 1 and keeps
live GitLab-only checks separate from local automated coverage.

## Current Status

Status: clean GitLab MR smoke verified; Phase 1 acceptance pending
duplicate-thread policy confirmation, manual/web pipeline, and final job-log
secret audit checks.

Upstream commit under validation: `4d83600 Support real Claude reviewer output`
with prerequisite backport `bc57be6 Backport AI review smoke fixes`.

Downstream smoke context:

- Date: 2026-06-30
- Repo path: `~/Repos/example-org/downstream-app`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`
- Merge request: `example-org/downstream-app!3122`
- Latest smoke pipeline: `178478`
- Latest smoke SHA: `699223c15f0d2271fac8c75432ad581e590e99c3`
- Smoke commit series includes `63ad47a5c Run AI review smoke with OpenRouter Claude`,
  `aa270e67e Prefer OpenRouter token for Claude Code`, and
  `d086dcf3f Read Claude Code stream output`.
- Continuation smoke includes `ba0a87c31 Record AI review post discussion
  references` and `699223c15 Add AI review phase 1 smoke marker`.
- Reviewer path: Claude Code CLI via OpenRouter.
- Downstream model config: `anthropic/claude-haiku-4.5`.
- Downstream `review_claude` configured `AI_REVIEW_REQUIRE_REAL_CLAUDE=1`,
  `AI_REVIEW_LOCAL_MOCK=0`, `ANTHROPIC_BASE_URL=https://openrouter.ai/api`,
  and `CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP=1`.

## Locally Verified

Verified in this repository on 2026-06-30:

- `make test` passed: 39 tests.
- `make lint` exited cleanly.
- `make consensus-local` produced a schema-valid consensus artifact.
- Unit coverage confirms:
  - Claude Code direct JSON, result JSON, fenced result JSON, and stream JSON
    parsing.
  - Stale-head posting returns `stale_head` without creating or updating
    discussions.
  - Same-head marker lookup skips unchanged existing discussions.
  - Real post results record `discussion_id` and `root_note_id` for created
    discussions.
  - Gate exits non-zero for synthetic `block_merge=true` and zero for
    stale-head pass-through.
  - Failed panels still write a consensus artifact.

Verified against private GitLab MR `example-org/downstream-app!3122`:

- Pipeline `178198` ran on source branch `ai-review-smoke-throw-away` at
  `053cb41577632e2e9becb488ce7443416849c02e`.
- The AI review job chain succeeded:
  - `prepare_ai_review`: job `2513987`
  - `review_claude`: job `2513988`
  - `consensus_ai_review`: job `2513995`
  - `post_ai_review`: job `2513996`
  - `ai_review_gate`: job `2513997`
- The overall pipeline ended `manual` because unrelated app jobs were waiting
  for manual action; the AI review jobs above had already completed
  successfully.
- `review_claude` installed and invoked Claude Code CLI `2.1.197`.
- `consensus.json` had `panel_status=full`, `successful_reviewers=["claude"]`,
  one surfaced correctness finding, and `block_merge=false`.
- `post_result.json` had `status=success`, `created_discussions=1`, matching
  `head_sha` / `current_head_sha`, and one `posted_discussions` entry with
  `discussion_id=6f278eaf319c168d1f94a4e17a90002c14e0d5b6` and
  `root_note_id=165912`.
- The MR contains an AI review `DiffNote` on `src/foo.py` line 2 with an
  `ai-review:v1` marker from run `gl-178198-2513987`.
- `gate_result.json` had `status=passed`, `reason=no_blocking_consensus`, and
  `block_merge=false`.

Verified from downloaded GitLab artifacts for continuation pipeline `178478`
on 2026-07-01:

- Pipeline `178478` ran on source branch `ai-review-smoke-throw-away` at
  `699223c15f0d2271fac8c75432ad581e590e99c3`.
- `manifest.json` recorded target branch `ai-review-poc-throw-away`, base SHA
  `48f797a7dc92c4a16c5fd3e3113201cc0c880263`, and run
  `gl-178478-2517606`.
- `review_claude` succeeded with model `anthropic/claude-haiku-4.5`.
- `consensus.json` had `panel_status=full`, `successful_reviewers=["claude"]`,
  one surfaced correctness finding, and `block_merge=false`.
- `post_result.json` had `status=success`, `created_discussions=0`,
  `updated_discussions=0`, matching `head_sha` / `current_head_sha`, and no
  `posted_discussions` entries. It also warned that summary fallback was
  required for a multiline anchor.
- `gate_result.json` had `status=passed`, `reason=no_blocking_consensus`, and
  `block_merge=false`.
- A generated-artifact scan excluding `inputs/repo_snapshot/` found no
  provider, GitLab across 13 generated files.
  A broader scan of the repo snapshot found only existing static source-tree
  matches: a redaction-test fixture and Firebase Google API keys.
- `PYTHONPATH=src python3 -m unittest discover -s tests` passed in the
  downstream `ai-review` package: 39 tests.

## Human Confirmation Needed

Confirm these against the private GitLab MR smoke run before marking Phase 1
accepted:

- [x] The MR project has `Pipelines must succeed` enabled. It was enabled after
  the 2026-06-30 smoke audit.
- [x] The pipeline uses separate `GITLAB_READ_TOKEN` and `GITLAB_WRITE_TOKEN`
  values. Confirmed by project owner.
- [x] `prepare_ai_review`, `review_claude`, `consensus_ai_review`,
  `post_ai_review`, and `ai_review_gate` all ran from the same MR pipeline.
- [x] `review_claude` invoked the real Claude Code CLI through OpenRouter, not
  the local mock path.
- [x] The MR pipeline posted a real inline discussion on the expected added
  line.
- [x] The post result stores both `discussion_id` and `root_note_id`.
- [x] Re-running the same head must not create duplicate threads for the same
  semantic issue. Continuation pipeline `178478` produced
  `created_discussions=0` and no `posted_discussions` entries.
- [ ] Multiple AI review threads on the same line are acceptable when they
  describe distinct issues.
- [ ] The observed two comments are not automatically duplicates; under the
  conservative policy, broad list/dict validation and narrow missing-key
  handling can remain separate threads.
- [ ] A manual or web pipeline worked with injected `AI_FLOW_INPUT`.
- [ ] No provider key, GitLab token, or appeared in job logs or
  persisted artifacts. Generated artifacts were scanned clean on 2026-07-01;
  GitLab job logs still need a final human security pass.

After these checks are confirmed, change the status above to:

```text
Status: Phase 1 accepted by private GitLab MR smoke on 2026-06-30.
```

## Next Phase

Start Phase 2 after Phase 1 acceptance is confirmed:

- Enable parallel `review_claude`, `review_codex`, and `review_opencode` jobs
  against the same immutable input bundle.
- Verify reviewer enable/disable is config-only.
- Verify one killed reviewer yields a degraded but valid consensus artifact.
- Verify zero successful reviewers fails before posting.
- Verify success, schema error, model error, wrapper timeout
  all emit valid findings/status artifacts.
