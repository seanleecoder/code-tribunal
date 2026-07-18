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
- Package description now covers GitLab merge requests and GitHub pull requests.
- GitLab web/API pipelines create AI review jobs only when a merge request IID is
  present, and the trust auditor now reserves the shipped Cursor jobs.
- State-load failure policy is now the explicit boolean
  `state.fail_closed_on_load_error`; state writes remain unconditionally fail-closed
  on overflow.

### Removed

- Removed hand-rolled YAML and JSON Schema fallback parsers; PyYAML and jsonschema
  are hard runtime dependencies and missing imports now fail fast.
- Removed the deprecated `GITLAB_READ_TOKEN` / `GITLAB_WRITE_TOKEN` fallback; only
  `GITLAB_TOKEN` is accepted for GitLab prepare and post.
- Removed the unused `python-gitlab` runtime dependency from the package and base image
  (the in-tree requests-based GitLab client is the only integration path).
- Removed the unused `respond` adapter stage, direct OpenRouter reviewer module,
  trigger helper, and the unproduced `skipped_advisory`, `unanchored`, and
  `superseded` contract values.
- Removed the inert `state.retention.overflow_behavior` and
  `state.retention.keep_superseded_runs` configuration keys.

### Migration

- Prepare rejects every symlink in the reviewed checkout when building
  `repo_snapshot`. Repositories that intentionally track symlinks must remove or
  replace them before review, or wait for a future non-followed link
  representation.
- Replace any remaining `GITLAB_READ_TOKEN` / `GITLAB_WRITE_TOKEN` CI variables with a
  single `GITLAB_TOKEN` project access token (`api` scope) used by prepare and post.
- The posted-body format is now `render-body.v2`. Existing bot-authored threads receive
  a one-time body update on the next review run.
- Replace legacy top-level `state.overflow_behavior: fail_closed` with
  `state.fail_closed_on_load_error: true`. The legacy key is accepted for one release
  with a deprecation warning. Remove `state.retention.overflow_behavior` and
  `state.retention.keep_superseded_runs` from custom configurations; they are now
  rejected as unknown keys.
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
