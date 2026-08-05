# Configuration reference

The shipped configuration is
[`ai-review/config/review.yaml`](../ai-review/config/review.yaml). Unknown keys
are rejected at every active mapping. Environment overrides are applied before
validation; set them at repository/project scope so every job derives the same
effective configuration.

## YAML keys

Defaults below are the shipped defaults. A custom configuration
must retain `schema_version: review_config.v1`.

### Reviewers

`reviewers.<name>` is repeated for `claude`, `codex`, `opencode`, and `cursor`.
All four are peer seats with the same contract; see
[Choosing the panel](#choosing-the-panel) for how to select which of them vote.

| Key | Type/default | Meaning |
|---|---|---|
| `schema_version` | string, `review_config.v1` | Configuration contract version; no other value is accepted. |
| `reviewers.<name>.enabled` | boolean | Whether the seat participates. Defaults: Claude/Codex/OpenCode true, Cursor false. Usually set through `AI_REVIEW_REVIEWERS` rather than per seat. |
| `reviewers.<name>.adapter` | path | Adapter below the image's `ai-review/` root. |
| `reviewers.<name>.model` | string | Provider model identifier passed to the adapter. |
| `reviewers.<name>.effort` | enum, optional | `low`, `medium`, `high`, `xhigh`, or `max`; Claude, Codex, and OpenCode forward all levels unchanged to their provider-specific effort setting. Provider/model rejection fails the reviewer rather than falling back silently. Cursor rejects this key. |
| `reviewers.<name>.timeout_seconds` | positive integer, `1800` | Whole review process-group timeout. |
| `reviewers.<name>.critique_timeout_seconds` | positive integer, optional (`timeout_seconds`) | Whole critique process-group timeout. Legacy `review_config.v1` files omit this key and resolve critique to `min(timeout_seconds, 900)` so the 20-minute CI ceiling is respected. |
| `reviewers.<name>.max_findings` | integer, `50` | Maximum raw findings admitted before consensus filtering. |
| `reviewers.<name>.credential_variable` | environment-variable name | Credential selected for this reviewer; not forwarded to other seats. |

### Choosing the panel

Every reviewer is a peer, so any of them may sit out. Select the panel with a
single roster variable:

```
AI_REVIEW_REVIEWERS=claude,codex,cursor
```

The seats named are enabled and every other configured seat is disabled. A seat
that sits out is not a failure: its adapter writes a `skipped` finding batch and
status without starting its CLI, and consensus accepts those artifacts. This
matters on GitLab, where the job graph is static and every seat's job is created
regardless of the roster.

Rules, all enforced at config load with a loud `ConfigError`:

- Two to four seats may be enabled. A one-seat panel is rejected — a single
  reviewer has nothing to corroborate against, so consensus would just pass one
  model's output through.
- Unknown or duplicated reviewer names are rejected, so a typo cannot silently
  shrink the panel.
- `AI_REVIEW_REVIEWERS` is mutually exclusive with the per-seat
  `AI_REVIEW_<REVIEWER>_ENABLED` flags. Setting both is an error rather than a
  precedence puzzle. The per-seat flags remain supported on their own; an empty
  value means "no override", which is how the canonical GitHub Actions workflow
  can map absent repository variables to `''`.
- Each selected seat needs its `credential_variable` present in the reviewer
  jobs, or that seat fails and the panel degrades. Cursor additionally requires
  `CURSOR_API_KEY`, which the canonical workflow supplies only to the Cursor
  matrix entry and only when Cursor is on the panel. A stale
  `AI_REVIEW_CURSOR_ENABLED=true` does not reopen that path: with a roster set the
  legacy flag is ignored for credential gating as well as for selection.

Roster changes are part of the effective-config digest, so a roster visible to
only some pipeline jobs fails the cross-stage consistency check instead of
producing a differently-sized panel per stage. Set it at repository/project or
group scope.

**Minimum runtime.** Every stage executes `/opt/ai-review` from the pinned base
and reviewer images, not from your checkout, so `AI_REVIEW_REVIEWERS` is honored
only by an image that ships roster support. What happens on an older pin differs
by platform:

- **GitHub Actions** fails loudly. Setting a roster makes the canonical workflow
  resolve the per-seat `AI_REVIEW_<REVIEWER>_ENABLED` variables to an empty
  string, and a runtime without empty-as-unset rejects that value, so `prepare`
  fails instead of reviewing with the wrong panel. Treat that failure as the
  signal to repin. Leaving the roster unset keeps the workflow fully compatible
  with older pins, because the per-seat variables then keep their previous
  literal defaults.
- **GitLab** ignores it silently. The template sets no per-seat flags of its own,
  so there is nothing for a stale runtime to reject: the roster is simply not
  read and the panel stays at whatever `review.yaml` enables.

Confirm the image pins before relying on the roster — mandatory on GitLab, where
nothing will tell you. The per-seat `AI_REVIEW_<REVIEWER>_ENABLED` flags work on
every supported runtime.

The blocking, resolution, and quorum thresholds are authored against the
configured reviewer count and take effect clamped to the enabled count, so
changing the roster never requires editing them in lock-step. With the shipped
values (`2`/`2`/`2`) the clamp is a no-op at every supported panel size. A
threshold exceeding the *configured* count is still rejected as an authoring
error.

The runner keeps a five-second reserve for process handling, so the shipped
values give each reviewer an effective adapter limit of about 1795 seconds for
review and 895 seconds for critique. The CI templates independently allow 40
minutes for review jobs and 20 minutes for critique jobs. These timeout values
are trusted image configuration; there is no timeout environment-variable
override. Both resolved stage values are included in the effective-config
summary and digest, so all pipeline stages must use the same policy.

For a legacy configuration without `critique_timeout_seconds`, the resolved
critique value is capped at 900 seconds even when `timeout_seconds` is higher.
The capped value, rather than the raw fallback input, is recorded in the
effective-config summary and digest. Explicit stage-specific values are used as
configured; keep them at or below 900 seconds while using the shipped 20-minute
critique CI ceiling.

OpenCode review and critique use a loopback-only `opencode serve` session client.
The client sends the fixed internal session title `code-tribunal-ai-review` and
the stage-specific schema through OpenCode's `json_schema` message format. The
title is non-empty and contains no prompt, repository, pull/merge-request, or
user data, so OpenCode does not make a separate automatic title-inference model
request. It is not a configuration key or an additional model-selection
interface. `reviewers.opencode.model` and `AI_REVIEW_OPENCODE_MODEL` remain the
sole OpenCode model controls.

The OpenCode reviewer may read, glob, and grep inside its temporary review root
and nowhere else: the adapter denies OpenCode's `external_directory` permission,
which gates absolute paths outside that root. Its search tools use the pinned
ripgrep shipped on the image `PATH`, so no reviewer run downloads a search binary
at review time. Neither is a configuration key — there is no supported way for
project configuration to widen the reviewer's filesystem reach.

### Production model/effort recommendations

The shipped model defaults are intended to be safe starting points. For
production, choose one complete profile and set both the model and effort
override for every enabled seat at project/repository scope so all pipeline
stages see the same effective configuration. Each cell is `model` / `effort`.

| Profile | Claude | Codex | OpenCode |
|---|---|---|---|
| Value | `anthropic/claude-opus-5` / `low` | `openai/gpt-5.6-luna` / `max` | `meta/muse-spark-1.1` / `xhigh` |
| Balance | `anthropic/claude-opus-5` / `medium` | `openai/gpt-5.6-terra` / `max` | `x-ai/grok-4.5` / `high` |

Use the corresponding `AI_REVIEW_<REVIEWER>_MODEL` and
`AI_REVIEW_<REVIEWER>_EFFORT` variables from the environment-variable table
below. `max` reaches Codex as `model_reasoning_effort=max`; OpenCode forwards `max` in
its generated config as `reasoningEffort=max` when selected. These profiles cover
Claude, Codex, and OpenCode; Cursor takes no `effort` key because its reasoning
depth is encoded in the model variant. The shipped OpenCode guidance favors low or unset effort
for flash-class models, while these profiles intentionally use higher effort on
their listed non-flash routes.

GitLab creates jobs from the included YAML, so the static graph always contains
`AI review: [cursor]` and `AI critique: [cursor]` alongside the other three
seats, whatever the roster says. A seat that is not on the panel still gets its
jobs; they complete quickly with skipped artifacts and cast no vote. Cursor is
off in the shipped default roster because its backend is a second egress
destination, not because it ranks below the other seats. If the consumer is still including an older template ref,
setting the enablement variable cannot create jobs that are absent from that
template.

### Panel and severity

| Key | Type/default | Meaning |
|---|---|---|
| `panel.min_successful_reviewers_for_blocking` | integer, `2` | Operational seats required before findings may block. Bounded by the configured reviewer count; clamped to the enabled count. |
| `panel.min_successful_reviewers_for_resolution` | integer, `2` | Trustworthy empty-or-valid seats required for absence-based resolution. Same bound and clamp. |
| `panel.quorum.votes_required` | integer, `2` | Agreeing reviewer votes required for quorum; minimum is two, since a one-seat panel is rejected. Same bound and clamp. |
| `severity_policy.single_reviewer_blocker.categories` | list, `[security, correctness]` | Categories eligible for the single-reviewer blocker policy. |
| `severity_policy.quorum_blocker.block_merge` | boolean, `true` | Permit quorum-backed blocker groups to set `block_merge`. |

Semantic grouping keys remain in the shipped YAML with `enabled: false` but are
**outside the 1.0 compatibility guarantee** (experimental). Do not enable them in
production. Environment overrides
`AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED` and
`AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD` are rejected.

| Experimental YAML key | Type/default | Meaning |
|---|---|---|
| `panel.grouping.semantic.enabled` | boolean, `false` | Opt-in deterministic title/body similarity grouping; unsupported for 1.0. |
| `panel.grouping.semantic.threshold` | number, `0.5` | Jaccard threshold from 0.0 through 1.0 when experimental grouping is enabled. |

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
| `state.fail_closed_on_load_error` | boolean, `false` | Fail prepare instead of starting with empty state after a load error. Enforcing installs should set `true`. |
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

## Environment variables

This is the canonical reference for Code Tribunal-owned runtime variables.
Provider and platform credentials are secrets; never print them or place them in
artifacts.

### Supported operator controls

| Variable | Default/source | Scope and validation |
|---|---|---|
| `AI_REVIEW_REVIEWERS` | unset (YAML `enabled` values) | Comma-separated panel roster over the configured reviewer names; enables exactly those seats and disables the rest. Two to four seats; unknown or duplicated names are rejected. Mutually exclusive with the per-reviewer `*_ENABLED` flags. Requires an image that ships roster support: an older pin fails `prepare` on GitHub and ignores the roster silently on GitLab — see [Choosing the panel](#choosing-the-panel). |
| `AI_REVIEW_CLAUDE_MODEL` | YAML model | Non-empty string; model identifier characters are adapter-validated. |
| `AI_REVIEW_CODEX_MODEL` | YAML model | Same. |
| `AI_REVIEW_OPENCODE_MODEL` | YAML model | Same. |
| `AI_REVIEW_CURSOR_MODEL` | `auto` | Exact Cursor model slug; Cursor effort is encoded in the model variant. `auto` is discovery-only and is not valid Cursor-enablement evidence. |
| `AI_REVIEW_CLAUDE_ENABLED` | YAML `enabled` (`true`) | Exact lowercase `true` or `false`; empty means no override. Rejected alongside `AI_REVIEW_REVIEWERS`. |
| `AI_REVIEW_CODEX_ENABLED` | YAML `enabled` (`true`) | Same. |
| `AI_REVIEW_OPENCODE_ENABLED` | YAML `enabled` (`true`) | Same. |
| `AI_REVIEW_CURSOR_ENABLED` | YAML `enabled` (`false`) | Same; also requires `CURSOR_API_KEY`. |
| `AI_REVIEW_CLAUDE_EFFORT` | YAML/provider default | Closed effort enum. |
| `AI_REVIEW_CODEX_EFFORT` | provider default | Closed enum; `low`, `medium`, `high`, `xhigh`, and `max` reach Codex as `model_reasoning_effort`. The selected model route must accept the level; forwarding does not probe provider compatibility. |
| `AI_REVIEW_OPENCODE_EFFORT` | provider default | Closed enum; `low`, `medium`, `high`, `xhigh`, and `max` reach OpenCode unchanged as `reasoningEffort`. The selected model route must accept the forwarded level; provider rejection fails the reviewer. |
| `AI_REVIEW_CRITIQUE_ENABLED` | `true` | Exact boolean; also controls GitLab critique job creation. |
| `AI_REVIEW_MERGE_GATE_ENABLED` | `true` | Exact boolean; disables finding blocking only. |
| `AI_REVIEW_POSTING_MODE` | YAML | `gitlab_discussions` or `github_reviews`. |
| `AI_REVIEW_STATE_BACKEND` | YAML | `gitlab_mr_state_note` or `github_pr_comment`; must match posting mode. |
| `AI_REVIEW_MANUAL` | unset | CI trigger control; only exact `true` selects manual behavior. |
| `AI_REVIEW_GITHUB_BOT_LOGIN` | `github-actions[bot]` in canonical workflow | Expected author of GitHub state comments. |

### Credentials

| Variable | Visibility | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | reviewer jobs only | OpenRouter authentication for Claude/Codex/OpenCode. |
| `ANTHROPIC_AUTH_TOKEN` | Claude reviewer only | Alternate Claude authentication for the pinned OpenRouter route; the canonical templates derive it from `OPENROUTER_API_KEY`. |
| `ANTHROPIC_API_KEY` | Claude reviewer only | Native Anthropic credential recognized by the Claude CLI; cleared by the canonical OpenRouter route. |
| `CURSOR_API_KEY` | Cursor reviewer/critique job only | Cursor authentication and separate egress destination. The canonical GitHub workflow gates it on `matrix.reviewer == 'cursor'`, so the other seats' jobs never carry it. |
| `GITLAB_TOKEN` | trusted prepare/post jobs | GitLab API access with `api` scope. |
| `GITHUB_TOKEN` | trusted prepare/post jobs | GitHub API access supplied by Actions. |
| `GH_TOKEN` | trusted GitHub prepare/post jobs | Local or custom-workflow fallback when `GITHUB_TOKEN` is absent. |
| `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | trusted post job only | Fine-grained token with Pull requests read/write and Metadata read; optional for personal-repository owner commands, but required when the built-in token cannot authorize collaborators (normally organization repositories) or mutate threads. |

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
| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED` | Semantic grouping is experimental YAML-only and outside the 1.0 compatibility guarantee. |
| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD` | Semantic grouping is experimental YAML-only and outside the 1.0 compatibility guarantee. |
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
| `AI_REVIEW_PACKAGED_RUNTIME` | Set by the base image; carries no production runtime behavior. |
| `AI_REVIEW_CONFIG` | Active configuration path. |
| `AI_REVIEW_INPUT_DIR` | Adapter input bundle path. |
| `AI_REVIEW_OUTPUT_DIR` | Adapter output root. |
| `AI_REVIEW_LOCAL_MOCK` | Test/preflight mock selector; production templates force `0`. Never set as a consumer project/pipeline variable. |
| `AI_REVIEW_ALLOW_LOCAL_MOCK` | Exact `true` required for every mock fallback, including a missing CLI/credential. Image preflight and operator evidence Chain B only; forbidden in production. This is a misconfiguration guard, not an authorization boundary: an actor who can inject both variables can enable mock mode. |
| `AI_REVIEW_MOCK_SCENARIO` | Selects a deterministic mock-reviewer finding set when the mock path runs (`default`, `blocking`, `blocking_alt`, `advisory`, `none`); ignored by the real reviewer CLIs and by production templates. |
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
| `AI_REVIEW_OPENCODE_ROOT` | Clean, disposable OpenCode working root passed to the loopback server client. |
| `AI_REVIEW_STREAM_ADAPTER_LOGS` | Internal diagnostic streaming switch; avoid in shared logs. |
| `XDG_CONFIG_HOME` | Disposable OpenCode configuration home created by the adapter. |
| `XDG_DATA_HOME` | Disposable OpenCode data home created by the adapter. |
| `OPENCODE_CONFIG_DIR` | Disposable trusted OpenCode configuration directory. |
| `OPENCODE_CONFIG_CONTENT` | Generated, restricted OpenCode configuration JSON. |

Build-only names, package-name variables, and
image tags belong to the release workflows, not the runtime configuration
surface.

| Build/preflight variable | Owner/purpose |
|---|---|
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
