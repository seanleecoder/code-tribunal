# Configuration reference

The shipped configuration is
[`ai-review/config/review.yaml`](../ai-review/config/review.yaml). Unknown keys
are rejected at every active mapping. Trusted prepare overlays image defaults,
custom YAML and operator environment overrides once, validates the result, and
records the complete immutable configuration in the input manifest. Review,
critique, consensus and post consume that configuration without reading YAML or
reapplying environment overrides. Credentials stay outside it.

## YAML keys

Defaults below are the shipped defaults. A custom configuration
must retain `schema_version: review_config.v4`.

### Reviewers

`reviewers.<name>` is repeated for `claude`, `codex`, `opencode`, and `cursor`.
Custom YAML may omit seats and individual fields: the shipped YAML supplies
their defaults, including disabled seats. The resolved roster always contains all
four first-party seats. They share the same deployment contract; see
[Choosing the panel](#choosing-the-panel) for how to select which of them vote.
Adapter paths, credential names, endpoint families, real-provider controls, effort
support, and supported stages are fixed by the trusted image's first-party reviewer
registry; they are not operator configuration.

| Key | Type/default | Meaning |
|---|---|---|
| `schema_version` | string, `review_config.v4` | Configuration contract version; no other value is accepted. |
| `reviewers.<name>.enabled` | boolean | Whether the seat participates. Defaults: Claude/Codex/OpenCode true, Cursor false. Usually set through `AI_REVIEW_REVIEWERS` rather than per seat. |
| `reviewers.<name>.model` | string | Provider model identifier passed to the adapter. |
| `reviewers.<name>.effort` | enum, optional | `low`, `medium`, `high`, `xhigh`, or `max`; Claude, Codex, and OpenCode forward all levels unchanged to their provider-specific effort setting. Provider/model rejection fails the reviewer rather than falling back silently. Cursor accepts only null effort; choose its model variant instead. |
| `reviewers.<name>.timeout_seconds` | positive integer, `1800` | Whole review process-group timeout. The summary's run details show it as each review job's limit and mark a job that used at least 80% of it. |
| `reviewers.<name>.critique_timeout_seconds` | positive integer, optional (`900`) | Whole critique process-group timeout. Omitting it resolves to 900 seconds regardless of `timeout_seconds`, so the 20-minute CI ceiling is respected. |
| `reviewers.<name>.max_findings` | integer, `50` | Maximum raw findings admitted before consensus filtering. Findings it cuts are reported as "omitted by the per-seat cap" in the summary's review health and grade the run `degraded`. |

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
regardless of the roster: a seat that is not on the panel still gets its
`AI review` and `AI critique` matrix instances, which complete quickly with
skipped artifacts and cast no vote.

Rules, all enforced at config load with a loud `ConfigError`:

- Three or four seats may be enabled. One- and two-seat panels are rejected. A
  finding surfaces only when two reviewer identities support it independently
  across review and critique, and a two-seat panel cannot reach that after losing
  a seat — the run would then report an empty review indistinguishable from a
  clean one.
- Unknown or duplicated reviewer names are rejected, so a typo cannot silently
  shrink the panel.
- The resolved configuration contains exactly the four first-party reviewer keys.
  Unknown names are rejected. Omitted seats inherit shipped defaults; use
  `enabled: false` or the roster variable to disable one.
- `AI_REVIEW_REVIEWERS` is the only environment-level panel selector. The YAML
  `enabled` values provide the default when it is unset. Retired per-seat
  `AI_REVIEW_<REVIEWER>_ENABLED` variables are rejected by name.
- Each selected seat needs the credential fixed for it by the trusted image:
  `OPENROUTER_API_KEY` for Claude, Codex, and OpenCode, or `CURSOR_API_KEY` for
  Cursor. If it is absent, that seat fails and the panel degrades. The canonical
  GitHub workflow supplies `CURSOR_API_KEY` only to the Cursor matrix entry and
  only when Cursor is on the roster; GitLab
  [differs](#credentials).

Cursor is off in the shipped roster because its backend is a second egress
destination, not because it ranks below the other seats: prompts, diffs, and any
snapshot content it reads reach Cursor rather than OpenRouter (the
[egress exception](../ai-review/images/SUPPLY_CHAIN.md#cursor-cli-egress-exception)).
Enable it only if you deliberately accept that path; `AI_REVIEW_CURSOR_MODEL`
selects its model.

Prepare captures the roster in the effective-config digest. Set it at
repository/project or group scope so prepare receives it. Later job environments
cannot change this run's roster.

The resolution threshold is authored against the configured reviewer count and
takes effect clamped to the enabled count, so changing the roster never requires
editing it in lock-step. With the shipped value (`2`) the clamp is a no-op at
every supported panel size. A threshold exceeding the *configured* count is
still rejected as an authoring error.

The runner keeps a five-second reserve for process handling, so the shipped
values give each reviewer an effective adapter limit of about 1795 seconds for
review and 895 seconds for critique. The CI templates independently allow 40
minutes for review jobs and 20 minutes for critique jobs. These timeout values
are trusted image configuration; there is no timeout environment-variable
override. Both resolved stage values are included in the effective-config
manifest and digest, so all pipeline stages must use the same policy. Explicit
`critique_timeout_seconds` values are used as configured; keep them at or below
900 seconds while using the shipped 20-minute critique CI ceiling.

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

### Shipped defaults

These cheap, reproducible models provide a safe starting configuration for
compatibility checks and real-provider smoke. Choose a production profile below
for stronger review quality.

| Seat | Model | Effort | Enabled |
|---|---|---|---|
| Claude | `anthropic/claude-haiku-5.5` | `low` | yes |
| Codex | `openai/gpt-6-luna` | `low` | yes |
| OpenCode | `deepseek/deepseek-v4.1-flash-1017` | `low` | yes |
| Cursor | `composer-2.5` | named-model default | no |

Each release's Candidate Canary panel runs real review and critique on the release
images with the shipped efforts, and its summary records each seat's model; see
[effort routes and the default smoke](evidence/RUNBOOK.md#effort-routes-and-the-three-seat-default-smoke).
The [release checklist](development/release-process.md#release-pull-request-checklist)
sets when a separate smoke is needed.

### Production model/effort recommendations

Model recommendations last reviewed: 2026-10-04.

Choose one complete profile at project/repository scope so prepare captures it. Set the model override for each enabled
seat. Set its effort override to the profile's level, or to `unset` when the
profile requires absent effort; model-only overrides inherit YAML effort (see
[effort inheritance and clearing](#effort-inheritance-and-clearing)).
Each cell is `model` / `effort`; Cursor is optional.

| Profile | Claude | Codex | OpenCode | Cursor optional |
|---|---|---|---|---|
| **Budget** | `anthropic/claude-sonnet-5.5` / `medium` | `openai/gpt-6-luna` / `max` | `xiaomi/mimo-v2.6-flash` / unset | `composer-2.5` |
| **Value / Balance** | `anthropic/claude-sonnet-5.5` / `xhigh` | `openai/gpt-6.1-sol` / `medium` | `xiaomi/mimo-v2.6-pro` / unset | `grok-4.7-medium` |

Use the corresponding `AI_REVIEW_<REVIEWER>_MODEL` and supported
`AI_REVIEW_<REVIEWER>_EFFORT` variables from the environment-variable table
below. For both MiMo models, **unset** means the resolved configuration records
`effort: null` and the adapter omits the provider effort setting. The shipped YAML sets OpenCode effort to `low`, so set
`AI_REVIEW_OPENCODE_EFFORT=unset` as described in
[effort inheritance and clearing](#effort-inheritance-and-clearing). MiMo uses the existing
OpenCode → OpenRouter route and `OPENROUTER_API_KEY`. Other OpenCode models
retain the generic effort support described below.

For more review quality, raise Claude Sonnet 5.5 to `max` or GPT-6.1 Sol to
`xhigh`. For Cursor Grok 4.7 / `xhigh`, use `grok-4.7-xhigh`, confirmed by
`cursor-agent --list-models` on CLI `2026.10.01-e373342`. That CLI exposes
`grok-4.7-{low,medium,high,xhigh}` and corresponding `-fast` variants; it does
not list a bare `grok-4.7` selector. Recheck the list when refreshing the CLI.
Cursor has no separate Code Tribunal effort variable;
reasoning depth is encoded in the model variant. Keep `composer-2.5` for smoke
and compatibility. `auto` remains a supported operator override, but should not
be used for reproducible CI or evidence.

Recommendations do not establish release validation: MiMo Pro and the Grok 4.7
variants have no live evidence on the current pins (earlier route canaries are at
[`v2.0.3`](https://github.com/seanleecoder/code-tribunal/tree/v2.0.3/docs/evidence)).
MiMo Pro's review job took nearly 13 minutes in that canary, so check reviewer timeouts.
Before claiming one validated, run a real review and critique with no silent
fallback: a
[route panel](evidence/RUNBOOK.md#effort-routes-and-the-three-seat-default-smoke)
for MiMo, and the
[Cursor model-specific check](evidence/RUNBOOK.md#cursor-model-specific-check) for
Grok.

### Panel

| Key | Type/default | Meaning |
|---|---|---|
| `panel.min_successful_reviewers_for_resolution` | integer, `2` | Trustworthy empty-or-valid seats required for absence-based resolution. Bounded by the configured reviewer count; clamped to the enabled count. |

Surfacing is not configurable. A grouped finding surfaces when at least two
unique reviewer identities support it independently across direct review and
critique; severity, including `blocker`, is an impact label that changes no
decision. There is no `severity_policy` object and no quorum setting.

Grouping carries no text-similarity setting. Findings are grouped on identity
that survives rewording — path, category, side, context hash, fingerprints, and
symbol — so two reviewers describing one bug in different words group when they
anchor to the same code, not when they happen to choose similar phrasing.

### Critique

One blind critique round always runs for each enabled seat. Failed or missing
critique evidence degrades run health; it does not suppress valid reviews.

| Key | Type/default | Meaning |
|---|---|---|
| `critique.allow_severity_downgrade` | boolean, `false` | Allow bounded downgrade policy; never crosses the blocker boundary. |

### Posting and state

| Key | Type/default | Meaning |
|---|---|---|
| `posting.mode` | enum, `gitlab_discussions` | `gitlab_discussions` or `github_reviews`. |
| `posting.inline_multiline` | boolean, `true` | Permit multiline inline comments. |

All three supported diff sides (`new`, `old`, `unchanged`) are eligible for
inline placement. FYI findings always go to the summary. Posting always refuses
stale-head mutations; these behaviors have no configuration switches.

Persistent cross-run state is always active and has no backend setting.
`posting.mode` selects the adapter that stores it inside the bot's summary
comment on GitLab or GitHub. Checksums and author checks are mandatory. Prepare
and post stop on inaccessible, corrupt, duplicate, or unmigrated existing state.
Remove `state.checksum_required`, `state.fail_closed_on_load_error`,
`state.recover_from_discussion_markers`, the per-status retention keys, and
`state.retention.max_state_bytes` when upgrading; the platform comment limit
bounds the ledger, and a summary whose health section and complete ledger
cannot fit fails before any platform write. Interrupted publication is
reconciled from bot-authored thread markers on the current revision. Active
records and human dispositions are always kept; only the newest five
automatically resolved records are retained.

| Key | Type/default | Meaning |
|---|---|---|
| `state.retention.max_records` | integer, `200` | Total record cap. |

### Limits

| Key | Type/default | Meaning |
|---|---|---|
| `limits.max_diff_bytes` | integer, `250000` | Maximum complete diff accepted for review. |
| `limits.max_files` | integer, `200` | Maximum changed files. |
| `limits.max_fyi_findings` | integer, `50` | Maximum FYI findings listed in the summary comment; the section renders a visible "more" trailer when it truncates. There is no equivalent cap on surfaced threads — every anchorable surfaced finding becomes a thread, and the volume bound is each reviewer's `max_findings`. |
| `limits.max_prompt_bytes` | integer, `500000` | Maximum rendered prompt bytes sent to a model. |

External-fork secret-bearing preparation is always refused. The canonical
GitHub workflow also skips fork pull requests. There is no safety opt-out.

## Platform differences

Both platforms use the same configuration, reviewer adapters, schemas, consensus
policy, and posting reconciliation. Platform credentials never enter reviewer
subprocess environments. The transport-specific surface is:

| Concern | GitLab | GitHub |
|---|---|---|
| Installation | Protected direct include | Small caller pinned to the reusable workflow's release SHA |
| Trusted workflow | Protected template project/ref and variable boundary | Base-branch `pull_request` workflow; never `pull_request_target` |
| Review target | Merge request | Pull request |
| Inline posting | Discussions/DiffNotes | Pull-request review comments |
| Summary/state | MR notes, authored by the token bot | PR issue comments, authored by the configured bot login |
| Commands | Reply in a finding discussion; Developer/30+ | Reply to the root inline comment; verified repository write authority |
| Thread resolution | Discussion API | GraphQL; optional fine-grained resolve token |
| Merge enforcement | None; repository policy may require successful pipelines | None; a ruleset may require the publication check |
| Forks | Protected variables are withheld; topology controls trusted execution | External forks are skipped by the canonical workflow |
| Concurrency | MR-scoped post resource group | PR-scoped workflow concurrency; stale-head guard prevents old mutations |
| Diff collection | Paginated MR diff API with exact-path raw recovery | Immutable base/head comparison raw diff |
| Artifact retention | 7 days for prepare/review/critique, 30 days for consensus/post | Repository or organization Actions default |

The GitLab raw-diff compatibility fallback is accepted only when the response
reports `overflow=false`, every affected path has one exact match, and no
replacement is collapsed or too large. Prepare re-fetches the MR revision and
rejects any base, start, or head change. GitHub binds the selected PR head, the
checked-out commit, and fetched PR metadata before and after diff collection and
again before writing the manifest.

Model-authored values use the shared literal renderer on both platforms: scalar
values are code spans, prose is one code span per line, and suggestions are text
fences. Redaction and newline normalization happen before literal encoding, and
the trusted footer and marker remain outside truncated model content.

## Environment variables

This is the canonical reference for Code Tribunal-owned runtime variables.
Provider and platform credentials are secrets; never print them or place them in
artifacts.

### Supported operator controls

| Variable | Default/source | Scope and validation |
|---|---|---|
| `AI_REVIEW_REVIEWERS` | unset (YAML `enabled` values) | The only environment-level panel selector. A comma-separated roster enables exactly those seats and disables the rest. Three or four seats; unknown or duplicated names are rejected. See [Choosing the panel](#choosing-the-panel). |
| `AI_REVIEW_CLAUDE_MODEL` | YAML model | Non-empty string; model identifier characters are adapter-validated. |
| `AI_REVIEW_CODEX_MODEL` | YAML model | Same. |
| `AI_REVIEW_OPENCODE_MODEL` | YAML model | Same. |
| `AI_REVIEW_CURSOR_MODEL` | `composer-2.5` (YAML) | Cursor model selector; effort is encoded in the model variant. `auto` remains a supported operator override that delegates model choice to Cursor. Use an exact slug for reproducible CI and model-specific evidence. |
| `AI_REVIEW_CLAUDE_EFFORT` | `low` (YAML) | Closed effort enum, or `unset`; see [effort inheritance and clearing](#effort-inheritance-and-clearing). |
| `AI_REVIEW_CODEX_EFFORT` | `low` (YAML) | Closed enum; `low`, `medium`, `high`, `xhigh`, and `max` reach Codex as `model_reasoning_effort`, or `unset`; see [effort inheritance and clearing](#effort-inheritance-and-clearing). The selected model route must accept the level; forwarding does not probe provider compatibility. |
| `AI_REVIEW_OPENCODE_EFFORT` | `low` (YAML) | Closed enum; `low`, `medium`, `high`, `xhigh`, and `max` reach OpenCode unchanged as `reasoningEffort`, or `unset`; see [effort inheritance and clearing](#effort-inheritance-and-clearing). The selected model route must accept the forwarded level; provider rejection fails the reviewer. |
| `AI_REVIEW_POSTING_MODE` | YAML | `gitlab_discussions` or `github_reviews`. |
| `AI_REVIEW_MANUAL` | unset | CI trigger control; only exact `true` selects manual behavior. |
| `AI_REVIEW_GITHUB_BOT_LOGIN` | `github-actions[bot]` in canonical workflow | Expected author of GitHub state comments. |

#### Effort inheritance and clearing

Missing, empty, and whitespace-only effort variables inherit YAML effort, and so
do model-only overrides: changing only `AI_REVIEW_CODEX_MODEL` retains the
shipped `low`. The trimmed, lowercase value `unset` removes YAML effort for
Claude, Codex, and OpenCode, so the adapter omits the provider setting. It is an
instruction to resolve effort to null before validation, never a provider effort value:
YAML `effort: unset` is invalid. Cursor rejects `AI_REVIEW_CURSOR_EFFORT`,
including `unset`; choose its model variant instead.

For example, to change Codex's model while using its provider default effort:

```sh
export AI_REVIEW_CODEX_MODEL=openai/your-model
export AI_REVIEW_CODEX_EFFORT=unset
```

Set these controls where prepare can read them. Changing them after prepare
only affects a subsequent run.

### Credentials

| Variable | Visibility | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | reviewer jobs only | OpenRouter authentication for Claude/Codex/OpenCode. |
| `ANTHROPIC_AUTH_TOKEN` | Claude adapter internal | Derived unconditionally by the Claude adapter from `OPENROUTER_API_KEY`; do not configure it as the seat credential. |
| `CURSOR_API_KEY` | Cursor reviewer/critique job only | Cursor authentication and separate egress destination. The canonical GitHub workflow gates it on `matrix.reviewer == 'cursor'`, so the other seats' jobs never carry it. |
| `GITLAB_TOKEN` | trusted prepare/post jobs | GitLab API access with `api` scope. |
| `GITHUB_TOKEN` | trusted prepare/post jobs | GitHub API access supplied by Actions. |
| `GH_TOKEN` | trusted GitHub prepare/post jobs | Local or custom-workflow fallback when `GITHUB_TOKEN` is absent. |
| `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | trusted post job only | Fine-grained token with Pull requests read/write and Metadata read; optional for personal-repository owner commands, but required when the built-in token cannot authorize collaborators (normally organization repositories) or mutate threads. |

The "visibility" column describes what reaches the **adapter process**, which is
where per-seat credential isolation is enforced: the runner builds each adapter's
environment from scratch and copies only the credentials declared by the trusted
first-party registry.

Do not read it as a description of job-level secret scoping, which differs by
platform. The canonical GitHub workflow gates `CURSOR_API_KEY` on the matrix
entry, but passes `OPENROUTER_API_KEY` to all four reviewer jobs. The GitLab
template injects no credentials at all — project and group CI/CD variables are
inherited by every job in the pipeline. On GitLab the adapter environment is
therefore the only boundary, not a second layer behind a job-level one.

### Platform and provider runtime

Canonical templates set these values. They matter to GHES, self-managed GitLab,
provider routing, and local adapter troubleshooting; consumers should not place
untrusted endpoints in merge-request-controlled configuration.

| Variable | Default/source | Purpose |
|---|---|---|
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST endpoint; Actions supplies the GHES value. |
| `CI_API_V4_URL` | GitLab predefined variable | Preferred GitLab v4 API endpoint. |
| `GITLAB_API_URL` | none | Fallback GitLab API endpoint for custom runtimes without `CI_API_V4_URL`. |

Reviewer provider endpoints are **not** configuration and are listed with the
internal runtime variables below. Each seat declares an endpoint family in the
trusted registry (`ai_review/reviewers.py`) and the adapter runner supplies the one
accepted host for that family, so nothing reads either name from the environment:
setting one has no effect, because an ambient value is overridden rather than
rejected. Native Anthropic routing is not supported.

Prepare rejects unsupported `AI_REVIEW_` names, including typos and removed
controls, with one unknown-variable diagnostic. Cursor effort is unsupported;
choose its model variant. Migration instructions are [below](#next-major-migration).

### Template and internal runtime variables

These are set by canonical templates or adapter dispatch. Consumers should not
override them in merge-request-controlled configuration.

| Variable | Owner/purpose |
|---|---|
| `AI_REVIEW_BASE_IMAGE` | GitLab template base image pin. |
| `AI_REVIEW_REVIEWER_IMAGE` | GitLab template reviewer image pin. |
| `AI_REVIEW_TRUSTED_IMAGE_SHA` | Source SHA bound to both GitLab image pins. |
| `AI_REVIEW_PACKAGED_RUNTIME` | Set by the base image; carries no production runtime behavior. |
| `AI_REVIEW_CONFIG` | Configuration path read only by prepare. |
| `AI_REVIEW_INPUT_DIR` | Adapter input bundle path. |
| `AI_REVIEW_OUTPUT_DIR` | Adapter output root. |
| `AI_REVIEW_LOCAL_MOCK` | Selects the deterministic mock in the Python runner only when `1`, with the companion allow flag below. Production templates set `0`; never set as a consumer project/pipeline variable. |
| `AI_REVIEW_ALLOW_LOCAL_MOCK` | Exact `true` required alongside `AI_REVIEW_LOCAL_MOCK=1`. A missing CLI or credential always fails when mock mode is not selected. Image preflight and the operator's mock lifecycle runs only; forbidden in production. This is a misconfiguration guard, not an authorization boundary: an actor who can inject both variables can enable mock mode. |
| `AI_REVIEW_MOCK_SCENARIO` | Selects a deterministic mock-reviewer finding set when the mock path runs (`default`, `blocking`, `blocking_alt`, `advisory`, `none`); forwarded only to the selected mock subprocess. |

| `AI_REVIEW_GITHUB_PR_NUMBER` | Immutable selected pull-request number passed to prepare. |
| `AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA` | Immutable selected pull-request head passed to prepare. |
| `AI_REVIEW_REVIEWER` | Selected adapter seat inside dispatch. |
| `AI_REVIEW_STAGE` | `review` or `critique` inside dispatch. |
| `AI_REVIEW_MODEL` | Effective model passed to one adapter. |
| `ANTHROPIC_BASE_URL` | Pinned Claude egress route, set by the runner to `https://openrouter.ai/api` (OpenRouter's Anthropic-compatible base — the Claude CLI has no other endpoint knob). Not read from the environment; an ambient value is overridden. |
| `OPENROUTER_BASE_URL` | Pinned Codex egress route, set by the runner to `https://openrouter.ai/api/v1`. Not read from the environment; an ambient value is overridden. OpenCode reads no endpoint variable: the same constant is a literal in its generated configuration. Cursor receives no endpoint variable — its CLI exposes none to pin. |
| `AI_REVIEW_EFFORT` | Effective effort passed to one adapter. |
| `AI_REVIEW_RENDERED_PROMPT` | Prompt file path passed to one adapter. |
| `XDG_CONFIG_HOME` | Disposable OpenCode configuration home, set by the OpenCode client for its server. |
| `XDG_DATA_HOME` | Disposable OpenCode data home, set by the OpenCode client for its server. |
| `OPENCODE_CONFIG_DIR` | Disposable trusted OpenCode configuration directory, set by the OpenCode client for its server. |
| `OPENCODE_CONFIG_CONTENT` | Generated, restricted OpenCode configuration JSON, set by the OpenCode client for its server. |

The runner owns mock selection and its allow guard. Real adapters receive no mock
controls and always require their CLI and credential.

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
| `AI_REVIEW_ROOT_DIR` | Internal shell path to the implementation root. |

Cursor Agent has no package-name variable: the image installs the tarball pinned,
with its SHA-256, in
[`ai-review/images/cursor-agent.pin`](../ai-review/images/cursor-agent.pin).

## Stage visibility and integrity

Prepare records every resolved default and override in `effective_config`, with
its whole-document `effective_config_sha256`. Later stages validate this complete
versioned document and digest at the input artifact boundary. They do not load
`AI_REVIEW_CONFIG` or resolve policy from their environment. Successful finding
and critique batches remain bound to the run and configuration digest; post also
checks the consensus artifact against the complete input-manifest digest.

Missing, incomplete, unsupported or corrupt manifest configuration fails before
consequential work: review/critique write `config_error`, consensus exits 3, and
post exits 1 before creating a platform client. Downstream environment changes
cannot alter the prepared roster, model, budget, platform or policy.

The digest detects mixed or corrupt artifacts; it does not authenticate a writer
that already controls a trusted job. Trusted CI delivery remains the boundary.

## Next-major migration

Upgrade the canonical workflow/include and both images together and start a new
run at prepare. Existing v3 configuration and old run artifacts are rejected.

- Set `schema_version: review_config.v4`. Retain supported reviewer overrides,
  resolution threshold, resource limits, state record cap, posting mode and
  multiline preference. Sparse reviewer configuration now inherits image defaults;
  use `effort: null` to clear a shipped effort in YAML.
- Delete `critique.enabled`, `critique.blind_reviewer_identity`,
  `posting.v1_inline_sides`, `posting.stale_head_guard`, `posting.fyi_mode`, and
  the `security` object. Their safe behavior is unconditional. Severity
  downgrading remains a separate opt-in policy pending the S11 decision.
- Delete `AI_REVIEW_CRITIQUE_ENABLED` from CI variables and custom rules. One
  blind critique round always runs. Unknown `AI_REVIEW_` variables fail prepare;
  older per-seat enable flags should be replaced by `AI_REVIEW_REVIEWERS`.
- Remove `--config` from consensus and post commands; only prepare accepts it.
  Keep operator overrides visible to prepare. Re-running a downstream job uses
  the prepared policy; start again at prepare to change it.
- Upgrading from 1.x requires rebuilding configuration from these supported keys,
  removing the old merge-gate job and any branch protection that requires it,
  and completing the explicit [state migration](operations.md#state-v2-migration).
  Runtime no longer contains per-version removal tables or environment tombstones.
