# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic
versioning.

## [Unreleased]

### Changed

- The OpenCode reviewer now obtains its batch through OpenCode's structured-output
  transport: a pinned loopback `serve` client sends the stage schema as
  `format: {"type":"json_schema", …}` and emits the reviewer batch directly, so
  OpenCode no longer depends on the model volunteering a schema-conforming
  payload. See
  [SPEC-50](docs/improvement-specs/spec-50-opencode-structured-reviewer-output.md).

- Reviewer image pins are refreshed to OpenCode `1.18.12`, Claude Code
  `2.1.221`, Codex `0.146.0`, and Cursor Agent `2026.07.23-e383d2b` with its
  artifact SHA-256 recorded and verified against the published download.

### Fixed

- Reasoning and tool parts are no longer read as answer text. A reviewer that
  wrote its findings only into its reasoning trace previously had that scratchpad
  scraped and rejected as `adapter output findings must be an array`, reporting a
  model outcome as malformed adapter output and yielding a zero-finding panel. A
  response with no answer part now fails as a model error that names the cause.

- Adapter output must now carry exactly one complete JSON root and no other JSON
  syntax. Previously a valid-looking payload was salvaged out of a response that
  also contained malformed JSON — `{"outer":{"findings":[]} BROKEN`,
  `{"a": nope} {"findings":[]}`, and `} prose {"findings":[]}` all yielded a
  batch the reviewer never nominated as its answer. Brace-free prose around the
  payload is still accepted, as are a simple bracketed label such as
  `[draft 1]` and an unmatched closer following a complete payload; prose that
  itself contains JSON syntax now fails closed with a schema error. The rule
  lives in one place (`ai_review.adapter_output`) and governs every adapter as
  well as the OpenCode text fallback.

- A parse or validation failure now also writes the complete redacted adapter
  stdout to `out/status/<stage>-<reviewer>-parse-raw-stdout.txt`, and the bounded
  preview keeps its newline structure. The previous preview elided the middle of
  the stream and collapsed its newlines, which is where a stream adapter's
  answer parts live.

- The runtime images no longer ship test code. `base.Dockerfile` copies only
  `ai-review/tests/fixtures`, which is all the preflight resolves paths from, and the
  build-time in-image test run is removed. Both CI preflights now bind-mount the
  checkout's tests into the built image, which still proves that exact image passes the
  suite — and proves it against the current tests rather than a frozen copy. A change
  to test *code* no longer alters image identity; fixtures still ship, so a fixture
  change does alter the image digest and remains part of the release binding.
- Release tags are signed with SSH from `v1.0.2` onward, verified against
  `.github/allowed_signers`. `v1.0.0` and `v1.0.1` are annotated but unsigned and are
  not being retagged.
### Removed

- `ai-review/ci/build-images.gitlab-ci.yml`, which built the product images from a
  GitLab mirror of this repository. No such project exists, no *current* user-facing
  documentation referenced it, it was outside the hashed release artifact, and it was
  never executed. The historical `PHASE_2_ACCEPTANCE.md` record does reference it; it
  is retained as written and now carries a superseded-procedure note. Its supply-chain guards are removed with it. GitLab support for
  *consumers* is unaffected: `review.gitlab-ci.yml` and `review-child.gitlab-ci.yml`
  are unchanged and remain covered by the live evidence campaign.

### Fixed

- `test_release_tools.py` no longer assumes the checked-in release artifact is an
  unbound draft. The repo-state guard is scoped by declared status — a draft must carry
  no verification binding, an active release must carry one — and the manifest tests
  derive the release version from the artifact instead of hardcoding it. The first
  assumption broke `make quality` on any release commit; the second broke it on every
  post-release draft reset. Its release-version derivation is also placed after the
  runtime-image skip guard, so importing the module inside an image that has no
  `release/` directory skips cleanly instead of raising `ImportError` — which broke the
  image build once before it was caught.

## [1.0.1] - 2026-07-30

### Known issues

- `make quality` on the 1.0.1 release commit fails
  `test_release_tools.py::test_draft_has_no_historical_verification_binding` and
  `test_populated_synthetic_draft_verification_remains_valid`. Both assert that the
  checked-in release artifact is an unbound draft, which a release commit must
  violate. Pre-existing since #95, not a 1.0.1 regression — the 1.0.0 release commit
  carries the same activated state. No product code, image, or evidence is affected;
  the tests are module-skipped inside the runtime image, whose own suite passes.
  Fix queued for 1.0.2. See [`release/1.0.1.md`](release/1.0.1.md).

### Changed

- Reviewer model IDs in `review.yaml` must fully match the supported model-ID
  grammar. A malformed YAML value, including a trailing newline, is rejected
  with `model_error` before the reviewer CLI is invoked.
- The Cursor permission smoke now requires an explicit exact model argument and
  rejects the discovery placeholder `auto` before invoking Docker.
- Posted review output now uses `render-body.v3`: free-text and path-shaped model
  values render as literal data, malformed suggestions remain visible as data, and
  fragment-aware truncation keeps spans atomic, blocks closed, and trusted
  footers/markers intact. Consensus artifacts remain `consensus.v1`. (These entries
  were previously filed under 1.0.0; `render-body.v3` landed after that tag and has
  never shipped in a release.)
- Prose in posted reviews now wraps instead of scrolling horizontally. Bodies,
  evidence, and critique rationale render as a paragraph of one code span per line
  rather than a `text` fenced block, which reads the same but reflows at the comment
  width on both GitLab and GitHub. Suggestions keep the fenced block, because they
  are code. Every model-authored value still renders inside a `code` or `pre`
  element — the boundary that also keeps model text away from both platforms'
  post-render autolink, mention, and issue-reference filters.

### Migration

- The posted-body format is `render-body.v3`. Existing bot-authored inline threads
  receive a one-time body update on the next review run; issue IDs, state records,
  and marker grammar remain unchanged. Deployments tracking `main` before this
  change see one further refresh, because the prose format changed within v3.

## [1.0.0] - 2026-07-25

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
- `AI_REVIEW_LOCAL_MOCK=1` now also requires `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
  including when a reviewer CLI or credential is missing. This prevents silent
  accidental fallback; production still relies on `AI_REVIEW_REQUIRE_REAL_*=1`
  because an actor able to inject both mock variables can enable mock mode.

### Changed

- Code Tribunal now declares container images and CI templates as its only
  supported distribution artifacts. Python modules remain internal container
  implementation details loaded from `/opt/ai-review/src`.
- Contributor tools are exactly pinned in `requirements-dev.txt` and covered by
  the repository supply-chain check.
- Schema-backed internal artifact types now match every shipped JSON schema,
  including critique, adapter-status, raw-finding, and state-alias artifacts.
  `make quality` is the canonical local and CI gate for Ruff, pytest with
  coverage, whole-package mypy, supply-chain validation, and compilation;
  installed checker failures can no longer fall through to successful fallback
  commands.
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
  As defense-in-depth for the SPEC-33 one-run binding, `evaluate_gate` now also
  requires the `post_result` to carry the same `run_id` as the consensus and
  fails closed on a missing, empty, or mismatched value. The gate CLI already
  schema-validates a required non-empty `run_id`, so this is a redundant
  in-function guard for direct callers rather than a reachable-bypass fix.
- The deterministic mock reviewer accepts `AI_REVIEW_MOCK_SCENARIO`
  (`default`, `blocking`, `blocking_alt`, `advisory`, `none`) to emit a chosen,
  schema-valid finding set when the mock path runs (`AI_REVIEW_LOCAL_MOCK=1`
  with `AI_REVIEW_ALLOW_LOCAL_MOCK=true`).
  `blocking_alt` shares identity with `blocking` (same title, category, and
  anchor) but a different body, so a lifecycle can exercise the changed-body
  in-place update deterministically. This lets validation and live-evidence
  lifecycle runs drive the real posting/state/gate path token-free and
  reproducibly instead of depending on a weak model to emit a usable finding.
  Ignored by the real reviewer CLIs and by production templates.
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
- GitLab prepare fetches MR diffs from the paginated `/diffs` endpoint. When
  GitLab collapses an otherwise reviewable file, prepare recovers that exact
  entry through the deprecated `/changes` raw-diff compatibility endpoint and
  still fails loudly if the fallback overflows, is ambiguous, or remains
  incomplete. Prepare revalidates the complete MR diff version after collection
  so paginated and fallback reads cannot silently mix revisions.
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
- Active release inputs now require cited evidence records to be exact
  `Status: passed` against the claimed runtime source and image digests (or an
  explicit `Release-evidence-waived` reason also registered under
  `verification.evidence_waivers` in the hashed release-inputs artifact).
  Release inputs are rejected at `status: active` unless that gate passes.
  Historical Identity prose is not a release binding; records must carry
  explicit `Release-*` fields, HTML comments are ignored when parsing evidence
  fields, and accepted waiver IDs/reasons are printed by the validator.
- Cursor is documented as experimental / outside the 1.0 evidence matrix.
  Semantic grouping env overrides are rejected; YAML keys remain disabled and
  outside the 1.0 compatibility guarantee.
- Adapter shell refusals that require `AI_REVIEW_ALLOW_LOCAL_MOCK=true` now
  surface as `config_error` rather than `model_error`.

### Fixed

- GitHub prepare now trusts only the exact resolved checkout for each Git command,
  allowing revision validation to run when a container uid differs from the owner
  of the runner-mounted workspace without changing global Git configuration.

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
- Consensus `category` inputs are now restricted to the finding-batch enum at the
  posting boundary. Pipeline-produced artifacts remain compatible; hand-edited or
  third-party artifacts with arbitrary categories must be corrected before posting.
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
- Remove any `AI_REVIEW_PANEL_GROUPING_SEMANTIC_*` environment overrides; semantic
  grouping is experimental YAML-only and those env names are rejected.
- Mock-enabled preflight or evidence jobs must set `AI_REVIEW_ALLOW_LOCAL_MOCK=true`
  alongside `AI_REVIEW_LOCAL_MOCK=1`. Never enable either on production consumer
  projects.
- `verification.evidence_waivers` is now required on
  `code_tribunal.release_inputs.v1` (use `{}` when unused). The same object is
  copied into the external release manifest; keep schema_version at `.v1`.

### Known issues

- **GitHub: pull requests that add or delete files can lose findings and fail the
  review.** GitHub renders an added file's diff with `--- /dev/null`, which anchor
  resolution rejects as an absolute path while scanning for the anchor's file. The
  affected findings are dropped; when every seat is affected, `consensus` exits 3,
  `post`/`gate` are skipped, and the required `gate` check cannot succeed. It
  triggers for a finding on an added or deleted file, or on a file ordered after one
  in the diff, and it affects real reviewers as well as the deterministic mock.
  GitLab is unaffected (its prepared diff uses `--- a/<path>`). No state is
  corrupted. Workaround: split file additions into a separate change request, or
  re-run once the added file has merged. Fix scheduled for 1.0.1. See
  [`release/1.0.0.md`](release/1.0.0.md) for detection details.

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
