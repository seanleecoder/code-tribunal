# Configuration reference

The shipped configuration is
[`ai-review/config/review.yaml`](../ai-review/config/review.yaml). Unknown keys
are rejected at every active mapping. Environment overrides are applied before
validation; set them at repository/project scope so every job derives the same
effective configuration.

## YAML keys

Defaults below are the shipped 1.0-candidate defaults. A custom configuration
must retain `schema_version: review_config.v1`.

### Reviewers

`reviewers.<name>` is repeated for `claude`, `codex`, `opencode`, and the
disabled-by-default `cursor` seat.

| Key | Type/default | Meaning |
|---|---|---|
| `schema_version` | string, `review_config.v1` | Configuration contract version; no other value is accepted. |
| `reviewers.<name>.enabled` | boolean | Whether the seat participates. Defaults: Claude/Codex/OpenCode true, Cursor false. |
| `reviewers.<name>.adapter` | path | Adapter below the image's `ai-review/` root. |
| `reviewers.<name>.model` | string | Provider model identifier passed to the adapter. |
| `reviewers.<name>.effort` | enum, optional | `low`, `medium`, `high`, `xhigh`, or `max`; adapters forward only supported levels. Cursor rejects this key. |
| `reviewers.<name>.timeout_seconds` | integer, `600` | Whole reviewer/critique process-group timeout. |
| `reviewers.<name>.max_findings` | integer, `50` | Maximum raw findings admitted before consensus filtering. |
| `reviewers.<name>.credential_variable` | environment-variable name | Credential selected for this reviewer; not forwarded to other seats. |

At least one reviewer must be enabled. The blocking, resolution, and quorum
thresholds must not exceed the enabled count.

GitLab creates jobs from the included YAML, so the static graph always contains
`AI review: [cursor]` and `AI critique: [cursor]` alongside the three default
seats. Cursor is a substitute seat used with OpenCode disabled, not an extra
default vote. When Cursor is disabled its jobs should complete quickly with
skipped artifacts. If the consumer is still including an older template ref,
setting the enablement variable cannot create jobs that are absent from that
template.

### Panel and severity

| Key | Type/default | Meaning |
|---|---|---|
| `panel.min_successful_reviewers_for_blocking` | integer, `2` | Operational seats required before findings may block. |
| `panel.min_successful_reviewers_for_resolution` | integer, `2` | Trustworthy empty-or-valid seats required for absence-based resolution. |
| `panel.quorum.votes_required` | integer, `2` | Agreeing reviewer votes required for quorum; minimum is two unless only one seat is enabled. |
| `panel.grouping.semantic.enabled` | boolean, `false` | Enable deterministic similarity grouping. |
| `panel.grouping.semantic.threshold` | number, `0.5` | Jaccard threshold from 0.0 through 1.0. |
| `severity_policy.single_reviewer_blocker.categories` | list, `[security, correctness]` | Categories eligible for the single-reviewer blocker policy. |
| `severity_policy.quorum_blocker.block_merge` | boolean, `true` | Permit quorum-backed blocker groups to set `block_merge`. |

### Critique

| Key | Type/default | Meaning |
|---|---|---|
| `critique.enabled` | boolean, `true` | Run blind peer assessment. |
| `critique.rounds` | integer, `1` | Must be 0 or 1 in v1. One round can affect consensus. |
| `critique.blind_reviewer_identity` | boolean, `true` | Replace reviewer identities with stable anonymous labels. |
| `critique.can_add_quorum_votes` | boolean, `false` | Must remain false in v1. Critiques are not reviewer votes. |
| `critique.allow_advisory_escalation` | boolean, `true` | Surface peer-supported advisory evidence without making it blocking. |
| `critique.allow_severity_downgrade` | boolean, `false` | Allow bounded downgrade policy; never crosses the blocker boundary. |

`critique.max_rounds` is not an active compatibility alias and is rejected.

### Posting, gate, and state

| Key | Type/default | Meaning |
|---|---|---|
| `posting.mode` | enum, `gitlab_discussions` | `gitlab_discussions` or `github_reviews`. |
| `posting.v1_inline_sides` | list, `[new, old, unchanged]` | Diff sides eligible for inline placement. |
| `posting.inline_multiline` | boolean, `true` | Permit multiline inline comments. |
| `posting.fallback_to_summary_comment` | boolean, `true` | Put unanchorable findings in a summary. |
| `posting.fyi_mode` | enum, `summary_comment` | Current destination for non-blocking FYI findings. |
| `posting.stale_head_guard` | boolean, `true` | Refuse mutations when the change-request head moved. |
| `merge_gate.enabled` | boolean, `true` | Enforce finding-based blocking. Operational post/state failures still fail. |
| `state.backend` | enum, `gitlab_mr_state_note` | GitLab default; GitHub requires `github_pr_comment`. |
| `state.recover_from_discussion_markers` | boolean, `true` | Reconstruct limited state if the state object is missing/corrupt. |
| `state.checksum_required` | boolean, `true` | Require checksum integrity on encoded state. |
| `state.fail_closed_on_load_error` | boolean, `false` | Fail prepare instead of starting with empty state after a load error. |
| `state.retention.keep_open` | boolean, `true` | Preserve open records. |
| `state.retention.keep_wontfix` | boolean, `true` | Preserve durable human dismissals. |
| `state.retention.keep_resolved_records` | integer, `5` | Maximum resolved records retained. |
| `state.retention.keep_stale_records` | integer, `2` | Maximum stale/stale-unverified records retained. |
| `state.retention.max_records` | integer, `200` | Total record cap. |
| `state.retention.max_state_bytes` | integer, `50000` | Encoded state payload byte cap. |

### Limits and security

| Key | Type/default | Meaning |
|---|---|---|
| `limits.max_diff_bytes` | integer, `250000` | Maximum complete diff accepted for review. |
| `limits.max_files` | integer, `200` | Maximum changed files. |
| `limits.max_posted_surface_findings` | integer, `25` | Maximum surfaced inline/fallback findings posted. |
| `limits.max_fyi_findings` | integer, `50` | Maximum FYI findings in the summary. |
| `limits.max_prompt_bytes` | integer, `500000` | Maximum rendered prompt bytes sent to a model. |
| `security.allow_external_fork_secrets` | boolean, `false` | Guard against provider/platform credentials in external-fork execution. Canonical GitHub workflows skip forks independently. |
| `security.snapshot_symlink_mode` | `reject` \| `skip`, `reject` | How `repo_snapshot` handles symlinks. `reject` fails closed on any symlink; `skip` omits them (never following or recreating), preserving containment for repositories that track benign symlinks. Under `skip`, omissions are reported to stderr (a bounded sample of paths plus the total count), the active mode is recorded in the prepare manifest's effective config, and the manifest also records `snapshot_skipped_symlink_count` and a bounded `snapshot_skipped_symlink_sample`; if the merge request changed a path that is omitted because it is (or is reached through) a symlink, prepare emits an elevated warning. |

## Environment variables

This is the canonical reference for Code Tribunal-owned runtime variables.
Provider and platform credentials are secrets; never print them or place them in
artifacts.

### Supported operator controls

| Variable | Default/source | Scope and validation |
|---|---|---|
| `AI_REVIEW_CLAUDE_MODEL` | YAML model | Non-empty string; model identifier characters are adapter-validated. |
| `AI_REVIEW_CODEX_MODEL` | YAML model | Same. |
| `AI_REVIEW_OPENCODE_MODEL` | YAML model | Same. |
| `AI_REVIEW_CURSOR_MODEL` | `auto` | Exact Cursor model slug; Cursor effort is encoded in the model variant. |
| `AI_REVIEW_CLAUDE_ENABLED` | `true` | Exact lowercase `true` or `false`. |
| `AI_REVIEW_CODEX_ENABLED` | `true` | Exact lowercase `true` or `false`. |
| `AI_REVIEW_OPENCODE_ENABLED` | `true` | Exact lowercase `true` or `false`. |
| `AI_REVIEW_CURSOR_ENABLED` | `false` | Exact lowercase `true` or `false`; requires `CURSOR_API_KEY`. |
| `AI_REVIEW_CLAUDE_EFFORT` | YAML/provider default | Closed effort enum. |
| `AI_REVIEW_CODEX_EFFORT` | provider default | Closed enum; unsupported levels leave provider default. |
| `AI_REVIEW_OPENCODE_EFFORT` | provider default | Closed enum; unsupported levels leave provider default. |
| `AI_REVIEW_CRITIQUE_ENABLED` | `true` | Exact boolean; also controls GitLab critique job creation. |
| `AI_REVIEW_MERGE_GATE_ENABLED` | `true` | Exact boolean; disables finding blocking only. |
| `AI_REVIEW_POSTING_MODE` | YAML | `gitlab_discussions` or `github_reviews`. |
| `AI_REVIEW_STATE_BACKEND` | YAML | `gitlab_mr_state_note` or `github_pr_comment`; must match posting mode. |
| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED` | `false` | Exact boolean. |
| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD` | `0.5` | Number from 0.0 through 1.0. |
| `AI_REVIEW_MANUAL` | unset | CI trigger control; only exact `true` selects manual behavior. |
| `AI_REVIEW_GITHUB_BOT_LOGIN` | `github-actions[bot]` in canonical workflow | Expected author of GitHub state comments. |

### Credentials

| Variable | Visibility | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | reviewer jobs only | OpenRouter authentication for Claude/Codex/OpenCode. |
| `ANTHROPIC_AUTH_TOKEN` | Claude reviewer only | Alternate Claude authentication for the pinned OpenRouter route; the canonical templates derive it from `OPENROUTER_API_KEY`. |
| `ANTHROPIC_API_KEY` | Claude reviewer only | Native Anthropic credential recognized by the Claude CLI; cleared by the canonical OpenRouter route. |
| `CURSOR_API_KEY` | Cursor reviewer jobs only | Cursor authentication and separate egress destination. |
| `GITLAB_TOKEN` | trusted prepare/post jobs | GitLab API access with `api` scope. |
| `GITHUB_TOKEN` | trusted prepare/post jobs | GitHub API access supplied by Actions. |
| `GH_TOKEN` | trusted GitHub prepare/post jobs | Local or custom-workflow fallback when `GITHUB_TOKEN` is absent. |
| `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | trusted post job only | Optional fine-grained GraphQL thread mutation token. |

### Platform and provider runtime

Canonical templates set these values. They matter to GHES, self-managed GitLab,
provider routing, and local adapter troubleshooting; consumers should not place
untrusted endpoints in merge-request-controlled configuration.

| Variable | Default/source | Purpose |
|---|---|---|
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST endpoint; Actions supplies the GHES value. |
| `CI_API_V4_URL` | GitLab predefined variable | Preferred GitLab v4 API endpoint. |
| `GITLAB_API_URL` | none | Fallback GitLab API endpoint for custom runtimes without `CI_API_V4_URL`. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Exact pinned endpoint accepted by Codex and OpenCode adapters. |
| `ANTHROPIC_BASE_URL` | unset, or `https://openrouter.ai/api` | Selects the Claude OpenRouter route; any other configured value is rejected. |

| Rejected variable | Reason |
|---|---|
| `AI_REVIEW_CURSOR_EFFORT` | Cursor selects reasoning depth through its model variant; a separate effort variable is rejected. |
| `GITLAB_READ_TOKEN` | Retired split-token path; configure one protected `GITLAB_TOKEN`. |
| `GITLAB_WRITE_TOKEN` | Retired split-token path; configure one protected `GITLAB_TOKEN`. |

### Template and internal runtime variables

These are set by canonical templates or adapter dispatch. Consumers should not
override them in merge-request-controlled configuration.

| Variable | Owner/purpose |
|---|---|
| `AI_REVIEW_BASE_IMAGE` | GitLab template base image pin. |
| `AI_REVIEW_REVIEWER_IMAGE` | GitLab template reviewer image pin. |
| `AI_REVIEW_TRUSTED_IMAGE_SHA` | Source SHA bound to both GitLab image pins. |
| `AI_REVIEW_TRUSTED_ROOT` | Trusted in-image root, `/opt/ai-review`. |
| `AI_REVIEW_CONFIG` | Active configuration path. |
| `AI_REVIEW_INPUT_DIR` | Adapter input bundle path. |
| `AI_REVIEW_OUTPUT_DIR` | Adapter output root. |
| `AI_REVIEW_LOCAL_MOCK` | Test/preflight mock selector; production templates force `0`. |
| `AI_REVIEW_REQUIRE_REAL_OPENROUTER` | Prevent missing provider prerequisites from falling back to mock behavior. |
| `AI_REVIEW_REQUIRE_REAL_CLAUDE` | Require the real Claude CLI. |
| `AI_REVIEW_REQUIRE_REAL_OPENCODE` | Require the real OpenCode CLI. |
| `AI_REVIEW_REQUIRE_REAL_CURSOR` | Require the real Cursor CLI. |
| `AI_REVIEW_GITHUB_PR_NUMBER` | Immutable selected pull-request number passed to prepare. |
| `AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA` | Immutable selected pull-request head passed to prepare. |
| `AI_REVIEW_REVIEWER` | Selected adapter seat inside dispatch. |
| `AI_REVIEW_STAGE` | `review` or `critique` inside dispatch. |
| `AI_REVIEW_MODEL` | Effective model passed to one adapter. |
| `AI_REVIEW_EFFORT` | Effective effort passed to one adapter. |
| `AI_REVIEW_RENDERED_PROMPT` | Prompt file path passed to one adapter. |
| `AI_REVIEW_STREAM_ADAPTER_LOGS` | Internal diagnostic streaming switch; avoid in shared logs. |
| `XDG_CONFIG_HOME` | Disposable OpenCode configuration home created by the adapter. |
| `XDG_DATA_HOME` | Disposable OpenCode data home created by the adapter. |
| `OPENCODE_CONFIG_DIR` | Disposable trusted OpenCode configuration directory. |
| `OPENCODE_CONFIG_CONTENT` | Generated, restricted OpenCode configuration JSON. |

Build-only names such as `AI_REVIEW_IMAGE_VERSION`, package-name variables, and
image tags belong to the release workflows, not the runtime configuration
surface.

| Build/preflight variable | Owner/purpose |
|---|---|
| `AI_REVIEW_IMAGE_VERSION` | Private GitLab image tag version slug. |
| `AI_REVIEW_BASE_TAG` | Base image build tag selected by publication tooling. |
| `AI_REVIEW_REVIEWER_TAG` | Reviewer image build tag selected by publication tooling. |
| `AI_REVIEW_IMAGE_TAG` | Shared publication/preflight image tag. |
| `AI_REVIEW_CLAUDE_NPM_PACKAGE` | Pinned Claude package name during image build. |
| `AI_REVIEW_CODEX_NPM_PACKAGE` | Pinned Codex package name during image build. |
| `AI_REVIEW_OPENCODE_NPM_PACKAGE` | Pinned OpenCode package name during image build. |
| `AI_REVIEW_REQUIRE_REAL_CODEX` | Image preflight requires the real Codex CLI. |
| `AI_REVIEW_ROOT_DIR` | Internal shell path to the implementation root. |

## Stage visibility and integrity

Configuration overrides that affect decisions must be visible to prepare,
review, critique, consensus, post, and gate. Prepare records an
`effective_config_sha256`; successful reviewer and critique evidence is bound to
it. Consensus exits 3 when consequential configuration, run identity, or
artifact identity differs. This digest detects pipeline misconfiguration; it is
not cryptographic authentication against a writer that already controls a
trusted job.

## 0.4.x migration summary

Remove inert/retired keys, rename record-count retention keys, remove
`critique.max_rounds`, use one `GITLAB_TOKEN`, and regenerate all stage artifacts
after changing configuration. The complete procedure is in
[operations](operations.md#upgrade-from-04x-to-10).
