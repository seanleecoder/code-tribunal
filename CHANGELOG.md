# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic versioning while it remains pre-1.0.

## [Unreleased]

### Security

- Prepare now builds `repo_snapshot` with a shared contained copier that never
  follows symlinks and rejects FIFO/socket/device nodes. Traversal requires
  `dir_fd`-relative `O_NOFOLLOW|O_DIRECTORY` opens (no path-based directory
  fallback). Hostile checkout links (including `/proc/self/environ`) cannot
  materialize prepare-job environment data into uploaded input artifacts.
  Repositories that intentionally track symlinks fail closed until a
  non-followed link representation exists. Snapshot directory depth is capped
  at 512; published `repo_snapshot` directories use mode `0755`. Contained prepare
  requires Linux/macOS `dir_fd` primitives (Windows local prepare fails closed).

### Changed

- Code Tribunal now declares container images and CI templates as its only
  supported distribution artifacts. Python modules remain internal container
  implementation details loaded from `/opt/ai-review/src`.
- Contributor tools are exactly pinned in `requirements-dev.txt` and covered by
  the repository supply-chain check.
- State retention controls are named for their actual units:
  `keep_resolved_records` and `keep_stale_records` retain bounded counts of
  records rather than run windows.
- Finding batches now record batch-quality fields (`raw_finding_count`,
  `accepted_finding_count`, `dropped_finding_count`, `usable_for_resolution`)
  and bind `effective_config_sha256`. Consensus panel seats and absence-based
  resolution use only reviewers with trustworthy empty-or-valid evidence;
  all-dropped malformed output cannot resolve open findings or manufacture
  panel success. Consensus artifacts expose `resolution_eligible_reviewers`.
  `failed_reviewers` now includes all-dropped-but-`success` adapters (they are
  not operational panel seats), which can weaken blocking and trip alerting that
  keys off failed-seat counts.
- Gate evaluation fails closed on post/state failures before consulting
  `merge_gate.enabled`. Advisory mode disables finding-based blocking only.
- Prepare records `effective_config_sha256` (misconfiguration detector for
  cross-job policy/env drift, not tamper-proofing). The digest covers reviewer
  models/toggles/`max_findings`, consequential panel/severity fields, and
  critique policy including `blind_reviewer_identity`. Consensus fails (exit 3)
  on consequential divergence, wrong run IDs, duplicate/disabled
  reviewer/critic evidence, success-batch model/digest mismatches, critique
  critic≠filename spoofing, unknown critique targets, or malformed consumed
  artifacts (including garbage JSON / schema errors). Digest checks are
  success-only; non-success batches with a mismatched digest degrade the panel
  instead of hard-failing the run. Consensus does not repair critique identity
  fields — missing/blank critics fail schema validation before accept.
  Unreadable/malformed JSON surfaces as `cannot read artifact`; programming
  errors in the reducer are not mapped to integrity exit 3.
- Posting now degrades update-path platform failures to summary fallback with a
  structured `partial_failed` result, and GitLab/GitHub HTTP clients retry
  idempotent GET/PUT/PATCH calls on 429/5xx/connection errors (including
  `requests` proxy/transport subclasses such as `ProxyError`). Exhausted
  connection failures on any verb, including non-retried POST, are normalized
  to platform API errors instead of raw transport exceptions.
- GitLab prepare fetches MR diffs from the paginated `/diffs` endpoint and fails
  loudly when GitLab marks any file as collapsed or truncated.
- Consensus groups now preserve reviewer suggestions and distinct evidence, and posted
  findings surface critique dispute rationales in a Dissent section.
- Posted findings and advisory summaries preserve complete model-authored content up to
  the GitLab or GitHub comment-size limit, with deterministic size-limit fallbacks.
- Project description now covers GitLab merge requests and GitHub pull requests.
- GitLab web/API pipelines create AI review jobs only when a merge request IID is
  present, and the trust auditor now reserves the shipped Cursor jobs.
- State-load failure policy is now the explicit boolean
  `state.fail_closed_on_load_error`; state writes remain unconditionally fail-closed
  on overflow.

### Removed

- Removed the incomplete Python distribution metadata, `py.typed` marker,
  package version export, and editable-install contributor workflow. The
  repository `pyproject.toml` now contains tool configuration only.
- Removed inert `critique.max_rounds`, deprecated top-level
  `state.overflow_behavior` compatibility, the ignored `access` argument from
  `create_runtime_platform`, and unused platform protocol shadow shapes.
- Removed hand-rolled YAML and JSON Schema fallback parsers; PyYAML and jsonschema
  are hard runtime dependencies and missing imports now fail fast.
- Removed the deprecated `GITLAB_READ_TOKEN` / `GITLAB_WRITE_TOKEN` fallback; only
  `GITLAB_TOKEN` is accepted for GitLab prepare and post.
- Removed the unused `python-gitlab` runtime dependency from the internal runtime set and base image
  (the in-tree requests-based GitLab client is the only integration path).
- Removed the unused `respond` adapter stage, direct OpenRouter reviewer module,
  trigger helper, and the unproduced `skipped_advisory`, `unanchored`, and
  `superseded` contract values.
- Removed the inert `state.retention.overflow_behavior` and
  `state.retention.keep_superseded_runs` configuration keys.

### Migration

- Finding-batch and critique-batch consumers must accept the new required
  quality/digest fields (`usable_for_resolution`, `effective_config_sha256`,
  and finding counts). Older finding batches without those fields are rejected
  at the consensus CLI boundary (exit 3) rather than treated as resolution-eligible.
- **All prior prepare `effective_config_sha256` digests are invalidated** by the
  expanded effective-config summary (`max_findings`,
  `critique_blind_reviewer_identity`, and related panel/severity/critique keys).
  Re-run prepare (do not reuse stale input artifacts) before consensus.
- Ensure `AI_REVIEW_*` overrides are scoped identically across prepare, review,
  critique, consensus, post, and gate jobs (project/group variables or workflow
  env). Job-scoped mismatches that used to warn now fail consensus. Changing
  panel quorum, severity policy, `max_findings`, or critique policy flags also
  changes the effective-config digest and requires a fresh prepare.
- Prepare rejects every symlink in the reviewed checkout when building
  `repo_snapshot`. Repositories that intentionally track symlinks must remove or
  replace them before review, or wait for a future non-followed link
  representation.
- Replace any remaining `GITLAB_READ_TOKEN` / `GITLAB_WRITE_TOKEN` CI variables with a
  single `GITLAB_TOKEN` project access token (`api` scope) used by prepare and post.
- The posted-body format is now `render-body.v2`. Existing bot-authored threads receive
  a one-time body update on the next review run.
- Replace legacy top-level `state.overflow_behavior: fail_closed` with
  `state.fail_closed_on_load_error: true`; the legacy key is now rejected.
  Remove `critique.max_rounds`. Rename `state.retention.keep_resolved_runs` and
  `state.retention.keep_stale_runs` to `keep_resolved_records` and
  `keep_stale_records`. Remove `state.retention.overflow_behavior` and
  `state.retention.keep_superseded_runs` from custom configurations; they are now
  rejected as unknown keys.
- Python consumers must switch to the supported digest-pinned containers and CI
  templates. Direct source imports remain available only as an unsupported
  contributor/testing mechanism.
- Ensure `panel.min_successful_reviewers_for_resolution` and
  `panel.quorum.votes_required` do not exceed the enabled reviewer count. When reducing
  the panel to one enabled reviewer, set the blocking, resolution, and voting thresholds
  to `1`.
- Consumers of the JSON schemas or Python types must remove the retired `respond`,
  `skipped_advisory`, `unanchored`, and `superseded` values before upgrading.

## [0.4.0] - 2026-07-14

### Changed

- The shipped configuration now contains only controls consumed by production
  code; inert policy, integration, and metadata placeholders were removed.
- Improvement specs now distinguish completed work, independently archived
  plans, and evidence-backed follow-up gaps.
- GitHub Actions now selects the GitHub posting/state backends at runtime, passes
  provider credentials only to model jobs, requires the real reviewer CLIs, and
  treats missing optional critique artifacts as a warning before consensus.
- Platform adapter construction now lives in a dedicated composition root rather
  than the posting and input-bundle CLI modules.
- The shipped GitHub Actions workflow now enables the merge gate by default.

### Removed

- Removed the no-op spend-control runtime and its associated artifact status.
- Removed the unwired issue-tracker helper and its unused state/post-result
  fields.

### Migration

- Custom review configurations must remove the former top-level `jira`,
  `budget`, `severity_order`, and `categories` keys before upgrading. They were
  reserved or inert rather than functional controls and are now rejected as
  unknown keys. Removed nested placeholders such as reviewer `cli_version`,
  panel/degradation metadata, posting marker/locking controls, declarative
  merge-gate settings, state marker versions, per-reviewer limits, and
  declarative security controls must also be removed. The shipped
  `ai-review/config/review.yaml` demonstrates the supported `review_config.v1`
  surface; unknown keys are rejected at every active mapping level.
- GitHub installations that need the previous advisory-only behavior must set
  `AI_REVIEW_MERGE_GATE_ENABLED=false`. Enforcing the gate in the workflow only
  blocks merges when its check is also required by the repository's branch
  protection rules or rulesets.

## [0.3.1] - 2026-07-13

### Added

- Protected child-pipeline entry point for compact GitLab parent pipelines.
- Platform-neutral review contracts, a GitHub platform adapter, and a safe
  GitHub Actions review workflow.
- Reproducible reviewer-image inputs and supply-chain pin validation.

### Changed

- The GitLab review DAG now uses one `ai_review` stage and identity-preserving grouped reviewer job names.
- Pipeline trust auditing now treats child `trigger:include` as a closed
  two-entry allowlist and requires an operator-supplied trusted project and full
  commit SHA. Child bridges must also disable inherited YAML variables and all
  downstream variable forwarding.
- GitLab artifact declarations no longer reference status files that commands do not create.
- Peer-supported advisory findings are surfaced by default through
  `critique.allow_advisory_escalation`; this does not add quorum votes or block
  merges.

### Fixed

- Package metadata now reports the release version instead of the original
  `0.1.0` baseline.
- Runtime-image preflight skips repository-only specification checks that are
  intentionally absent from the production image.

### Migration

- Reviewer jobs were renamed from `review_<reviewer>` and
  `critique_<reviewer>` to `AI review: [reviewer]` and
  `AI critique: [reviewer]`; update custom `needs`, overrides, dashboards, and
  scripts.
- The trust-audit CLI now requires `--mode`, `--template-project`, and
  `--template-sha`. Child mode requires two exact project includes pinned to one
  full commit SHA.
- Child bridges must set `inherit:variables: false`, define no bridge variables,
  and explicitly disable both YAML-variable and pipeline-variable forwarding.

## [0.3.0] - 2026-07-12

### Added

- Hermetic post-to-gate integration coverage, security seeds, and golden consensus snapshots.
- Optional deterministic semantic consensus grouping with a `panel_convergence` summary metric.
- Typed domain contracts across reducer, posting, gate, anchor, and GitLab client boundaries.

### Changed

- Decomposed consensus posting into typed, testable phases.
- Unified severity ordering and unified-diff parsing.

## [0.2.0] - 2026-07-11

### Added

- Apache-2.0 license and open-source project scaffolding.
- Pull request CI for linting, tests with coverage, and strict mypy slices.
- Trusted-pipeline audit tooling and operational runbook.

### Changed

- AI review `body_hash` includes `RENDER_BODY_VERSION`; posted Markdown is unchanged, but existing bot-authored discussion markers receive a one-time update after upgrade.
- Documentation distinguishes implemented behavior from future product ideas.
- Claude adapter endpoint handling requires the exact OpenRouter Anthropic base URL.
- Posted model-authored finding text is redacted before publication.

## [0.1.0] - 2026-07-10

### Added

- Initial public baseline for the CI-native multi-agent review pipeline.
