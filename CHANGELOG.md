# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic
versioning.

## [Unreleased]

### Changed

- Oversized review bodies now retain only complete priority-ordered fragments
  before the truncation notice; no model-authored prose or fenced block is cut
  partway through.
- Historical release inputs now come directly from
  `vX.Y.Z:release/release-inputs.json`; redundant checked-in snapshots are no
  longer created.
- Removed unused packaged and internal surfaces, including
  `adapters/validate_output.py`, the prompt-render CLI, and pre-landed rendering
  helpers.

- **Breaking: findings are informational and surface on independent support.**
  A grouped finding surfaces when at least two unique reviewer identities support
  it across direct review and critique. Severity, including `blocker`, is an
  impact label and changes no decision in either direction.

  The reducer used to decide the same policy in four places: a pre-critique
  quorum function, a post-critique recompute with an advisory-escalation path,
  the ambiguous state-match override, and the majority-noise drop. There is now
  one pure function in `ai_review/consensus_policy.py`, and one call site that
  assigns `decision`. Its branches are ordered by precedence: an ambiguous
  cross-run state match stays `fyi` whatever the support, majority independent
  noise then drops the group, and only after both does the two-support threshold
  apply.

  Against the shipped v2 defaults most decisions are unchanged — two direct
  reviewers already surfaced, and one reviewer plus one agreeing critic already
  surfaced through advisory escalation. Four things do change:

  - **A lone `blocker` in `security` or `correctness` no longer surfaces.** It
    was surfaced with `human_ack_recommended`; it is now `fyi`. This is the only
    change that reduces what maintainers see, and it is deliberate.
  - **A thin panel can now surface.** A panel below the old blocking minimum was
    forced to `fyi`; one successful review seat plus one independent agreeing
    critic now reaches two supporters and surfaces.
  - **Mixed verdicts from one critic collapse semantically.** Where grouping
    combines findings a critic critiqued separately, the surviving verdict used
    to be chosen by an incidental sort key under which `agree` beat `noise`. The
    strongest objection now wins, by the precedence
    `noise > dispute > duplicate > agree`. A critic still contributes at most one
    verdict, one support vote, and one severity request per group.
  - **`panel_status` loses `advisory_only`**, which described a panel below the
    blocking minimum. Status now reports execution health only and never alters
    the threshold. Absence-based cross-run resolution is unaffected: it has
    always been decided by `panel.min_successful_reviewers_for_resolution`
    against the resolution-eligible seats, never by panel status.

  Dissent is unchanged and remains first-class: an effective `dispute` keeps its
  critic, rationale, and optional adjusted severity, is rendered with the
  finding, and subtracts no support even when the group surfaces.

- **`render-body.v3` becomes `render-body.v4` — one bump for the whole series.**
  The footer heading is now `Support:` rather than `Consensus:`. It no longer
  reports direct votes, critique support, blocking, or human acknowledgement; it
  reports the direct reviewers, agreeing critics (or `none`), the independent
  support count, `Status: surfaced for discussion`, and that the merge decision is
  left to maintainers and downstream automation. The highest severity header may
  still read `BLOCKER`; the footer is what makes its informational meaning
  unambiguous.

  `parse_review_note` accepts **both** headings for one release, because marker
  recovery reads thread bodies written by the previous version; the `Consensus:`
  section boundary is a temporary compatibility entry with a removal target, not
  a permanent alias.

  The version string is a body-hash input, so **expect every pre-existing thread
  to be updated once** on the first v4 run instead of reported
  `skipped_unchanged`. That churn is cosmetic: finding identity lives in
  `issue_id` and the alias chain, and the persisted state schema carries none of
  the removed fields, so no state migration is required.

- **Minority dissent now outranks evidence and suggestion under truncation.**
  The renderer fits fragments in order and stops at the first one that does not
  fit, so position is priority. Dissent previously sat after evidence and could
  be dropped from an oversized body while the footer — which is never truncated —
  survived. The new footer is longer, which would have made that more likely. A
  body under platform pressure now loses supporting detail before it loses the
  argument against the finding.

- **Breaking: the `gate` required status check is gone. Remove it from branch
  protection before or together with this upgrade.**

  The installation guides used to instruct repositories to add the workflow's
  `gate` job as a required status check. That job no longer exists, and on GitHub
  a required check that never reports leaves pull requests **permanently
  unmergeable** — the workflow does not fail, it simply never produces the check
  the ruleset waits for. Delete the `gate` entry from every ruleset and
  branch-protection rule that names it, **before or together with** installing
  the new workflow. On GitLab, delete any custom `needs`, dashboard, or script
  naming the `ai_review_gate` job.

  A repository that wants the review to have *run* before a merge may require
  `post` instead, but it is not equivalent: `post` reports whether publication
  completed, not what the review found, and cannot cover a run whose `prepare`
  job never started.

- **`post` is the terminal stage and its exit status reports publication only.**
  `success` and `stale_head` exit 0; `failed`, `partial_failed`, and
  `state_overflow` exit nonzero. A finding of any severity, `blocker` included,
  exits 0. `stale_head` is a successful no-op: a newer revision superseded the
  run, so performing no mutation is correct. An **unrecognized** status also
  exits nonzero, so a status added later without revisiting `post.py` fails
  loudly instead of reporting a false success. `--dry-run` uses the same mapping.

- **`ai_review_gate` stays reserved in `scripts/pipeline_trust.py` for one
  release**, because a consumer pinned to an older template still declares the
  job and un-reserving a name loosens a trust boundary. Tracked for removal.

### Removed

- **Breaking: the merge gate is deleted.** `ai_review/gate.py`,
  `gate_result.schema.json`, `GateResult`, `GateStatus`, `test_gate.py`, the
  GitHub `gate` job, and the GitLab `ai_review_gate` job are gone, along with the
  gate artifact upload/download paths. See the operator migration above.

  Two behaviors disappear with it, both deliberately:

  - **The cross-artifact run-id check.** `evaluate_gate` re-verified that
    `post_result.run_id` matched `consensus.run_id` as SPEC-33 defense in depth.
    It existed because the gate was the one stage that recombined two
    independently downloaded artifacts. No stage does that now: `post.py` derives
    its result from the consensus it loaded in the same process, so a mismatch is
    unreachable rather than merely unlikely. Do not reintroduce the check
    elsewhere.
  - **Consumer-side validation of `post_result.json`.** The gate CLI was its only
    reader. `post.py` validates on write and nothing in the pipeline reads the
    artifact afterwards, so the write-side validation is now the only one — and
    is retained.

  On GitHub, `post` gains `if: always() && needs.prepare.result == 'success'` and
  a step that fails when consensus did not succeed, so a failed consensus
  produces a *failed* `post` rather than a skipped one. This is **not** mirrored
  on GitLab: **Pipelines must succeed** already enforces at pipeline level there,
  so a failed `consensus_ai_review` blocks and `post_ai_review` staying skipped is
  correct. The asymmetry follows from the platforms and is intentional.

- **Breaking: the artifact contract is now `consensus.v2`.** Groups gain
  `support_count` and `agreeing_critics`, and lose `vote_count`,
  `critique_support_count`, `critique_noise_count` (available as
  `critique_summary.noise`), `block_merge`, and `human_ack_recommended`. The
  summary keeps only `surface_count`, `fyi_count`, and `drop_count`, losing
  `block_merge` and `panel_convergence` — the latter was computed from direct
  quorum and has no clear meaning once direct and critique support are
  deliberately combined, and nothing in the runtime ever read it. Of the removed
  fields only `summary.block_merge` had a behavioral consumer; the rest were read
  by the thread footer or by the reducer itself. The removed fields are not
  reintroduced as optional compatibility fields.

- **Breaking: the configuration contract is now `review_config.v3`**, and every
  configuration must leave at least **three** reviewer seats enabled.
  `review_config.v2` is rejected with a message naming this migration.

  ### Migrating a `review_config.v2` document

  | v2 key | v3 | Why |
  |---|---|---|
  | `severity_policy` (whole object) | **delete** | Severity no longer affects any decision. `blocker` remains the highest impact label. |
  | `panel.min_successful_reviewers_for_blocking` | **delete** | There is no blocking verdict to gate. |
  | `panel.quorum` | **delete** | The support threshold is a product invariant, not an operator setting. Lowering it would make consensus a passthrough of one model's output. |
  | `critique.allow_advisory_escalation` | **delete** | An agreeing independent critic is simply a second supporter; there is no separate escalation path to enable. |
  | `merge_gate` (whole object) | **delete** | There is no merge gate. Publication health is reported by the `post` job's exit status. |
  | `posting.fallback_to_summary_comment` | **delete** | Summary fallback is now unconditional. Set to `false` it discarded every surfaced finding that could not be anchored — from the threads *and* from the summary — leaving it only in persisted state and a warning. A flag whose only reachable effect is losing product output is not a choice. |
  | `limits.max_posted_surface_findings` | **delete** | Every anchorable surfaced finding becomes a thread. The cap silently reclassified a finding two reviewers supported independently into summary-only because a configured count was reached. The volume bound is each reviewer's `max_findings`. `limits.max_fyi_findings` **stays**: it truncates a list that is already summary-only and renders a visible "more" trailer, which is a different thing. |
  | `reviewers.<name>.adapter` | **delete** | Adapter paths are fixed by the trusted first-party reviewer registry shipped in the image. |
  | `reviewers.<name>.credential_variable` | **delete** | Credential names and isolation are fixed by each seat's registry definition. Claude, Codex, and OpenCode use `OPENROUTER_API_KEY`; Cursor uses `CURSOR_API_KEY`. |
  | `state.backend` | **delete** | v2 accepted a restatement of the value it derived from `posting.mode`; v3 rejects the key outright, with any value. Persistent state is always active and `posting.mode` selects the adapter that stores it (`gitlab_discussions` → a GitLab MR state note, `github_reviews` → a GitHub PR comment). Nothing is written back into `state`, so a resolved configuration no longer carries the field at all. |
  | `AI_REVIEW_<REVIEWER>_ENABLED` environment variables | **unset and use `AI_REVIEW_REVIEWERS`** | One roster replaces four booleans that could express contradictory or accidental panel sizes. Retired names fail loudly so persisted repository/project variables do not become silent no-ops. |
  | fewer than three enabled reviewer seats | **enable a third seat** | Every critique seat comes from the same roster and self-critique cannot corroborate, so one seat can never reach two supporters. Two can, but not after losing one — and a seat that degrades silently is indistinguishable from a clean review. |
  | `schema_version: review_config.v2` | `review_config.v3` | |

  The shipped `ai-review/config/review.yaml` already enables exactly three seats
  (claude, codex, opencode; cursor off by default), so the floor does not change
  the shipped default. It **does** reject two-seat deployments that were valid
  under v2.

  Claude now supports the pinned OpenRouter Anthropic-compatible endpoint only.
  Native Anthropic credentials are no longer accepted; configure
  `OPENROUTER_API_KEY`.

  **Provider endpoints are no longer read from the environment.** Each reviewer
  seat declares an endpoint family in the trusted registry, and the adapter runner
  supplies the one accepted host for it — Claude gets
  `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, Codex and OpenCode get
  `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`, Cursor gets neither. Setting
  either variable now has no effect: an ambient value is overridden rather than
  rejected, so no caller has to know a URL that was never configurable. Both CI
  templates stop declaring the two variables; a consumer copy that still declares
  them keeps working. This replaces the endpoint validation that shipped earlier in
  this series, which rejected an unset `ANTHROPIC_BASE_URL` and so broke the
  reviewer-image publication preflight and every `make *-local` target defaulting
  to the claude seat.

  `critique.blind_reviewer_identity` and `critique.allow_severity_downgrade` are
  now type-checked as booleans, like `critique.enabled`. They were read through
  `bool()`, so the string `"false"` silently enabled them.

  The removals change `effective_config_summary`, and therefore the cross-stage
  effective-config digest — `state_backend` leaves it alongside the merge-gate
  field, for the same reason: it only restated `posting_mode`, which the digest
  already binds. Every stage recomputes the digest per run, so no artifact
  *format* migration is needed, but an in-flight pipeline that mixes shapes fails
  the drift check. Start a fresh run from `prepare` after upgrading; a pipeline
  whose stages all use one immutable runtime revision stays internally consistent.

  **`AI_REVIEW_MERGE_GATE_ENABLED` is now rejected by name**, not ignored. A run
  fails at config load while it is set, naming the migration. Delete it from
  every repository variable, GitLab project variable, and group variable — these
  outlive template revisions, which is exactly why a retired override must raise
  rather than become a silent no-op. The tombstone is a temporary-compatibility
  entry with a removal target, not a permanent fixture.

  **The effective-config digest changes for every configuration**, including one
  whose YAML you never touched, because `merge_gate_enabled` and `state_backend`
  leave `effective_config_summary()`. Consensus re-derives that digest as a cross-job
  drift detector, so a pipeline that mixes a pre-upgrade `prepare` manifest with
  post-upgrade `consensus` fails the drift check. **In-flight runs must be
  restarted from `prepare` after upgrading, not resumed.**

  `AI_REVIEW_STATE_BACKEND`, `AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED`, and
  `…_SEMANTIC_THRESHOLD` stay rejected by name. The v1 note below called them
  droppable at the next major release; v3 is that release, and the decision taken
  is to keep them. Silently ignoring a stale GitLab project variable is the exact
  failure these entries exist to prevent.

- **Breaking: the configuration contract is now `review_config.v2`.** Four keys
  that were never choices are gone. `review_config.v1` is rejected with a message
  naming this migration, rather than being accepted with a changed shape — a
  version whose meaning depends on the runtime reading it is not a contract.

  ### Migrating a `review_config.v1` document

  | v1 key | v2 | Why |
  |---|---|---|
  | `critique.rounds` | **delete** | A second boolean that had to agree with `critique.enabled` before critique ran. `critique.enabled` is now the only switch. |
  | `critique.can_add_quorum_votes` | **delete** | Validation rejected any value but `false`, and nothing read it. |
  | `panel.grouping.semantic.enabled`, `…threshold` | **delete** | An opt-in Jaccard comparison over finding titles and bodies. Shipped disabled, outside the 1.0 guarantee, with both environment overrides rejected by name. |
  | `state.backend` | **delete** | v2 derived the value from `posting.mode` and tolerated a matching restatement. v3 rejects the key with any value — see the v2 to v3 table above, which is the migration to follow if you are landing on the current release. |
  | `schema_version: review_config.v1` | `review_config.v2` | |

  A config copied from the shipped `ai-review/config/review.yaml` carries all of
  the deleted keys, so it needs this edit. One that never set them needs only the
  `schema_version` line.

  Grouping now rests entirely on identity that survives rewording — path,
  category, side, context hash, fingerprints, and symbol. The golden consensus
  fixture for the default path is byte-identical, so no finding, group, or
  decision changes.

  The removals change `effective_config_summary`, and therefore the cross-stage
  effective-config digest. Prepare, review, consensus, post, and gate recompute
  it per run, so no artifact migration is needed.

- **`AI_REVIEW_STATE_BACKEND` is retired and now rejected**, alongside
  `AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED` and
  `…_SEMANTIC_THRESHOLD`. Setting any of them raises a configuration error naming
  the replacement. GitLab project and group variables outlive the template
  revisions that read them, so a stale override has to fail loudly rather than be
  silently ignored after a repin. The rejections are a migration aid and may be
  dropped in the next major release.

- **The Cursor permission smoke is deleted** — `scripts/smoke_cursor_permissions.sh`
  and its unit suite, the `cursor-permission-smoke` publisher job, and the base-image
  `COPY`. The job was never wired into `publish`'s `needs`, and its
  `CURSOR_SMOKE_MODEL` was hardcoded to `auto`, the one value the script refuses,
  so it had never executed: 865 lines asserting a guarantee nobody held. Nothing
  now verifies the pinned CLI's *runtime* interpretation of the `Shell(*)` and
  write denies; repository tests still prove the policy is passed to every
  invocation, the CLI stays pinned by `cursor-agent.pin` and version-checked at
  build, and the exposure is bounded by the review workflow running only for
  same-repo heads, so the seat never processes fork content. `SUPPLY_CHAIN.md`
  states this gap plainly instead of describing a gate that did not exist.

- **Breaking: release inputs are now `code_tribunal.release_inputs.v2`.** v2 is
  v1 without the per-file-set `hashes` member. The six aggregate SHA-256 groups
  were compared against hashes recomputed from the same checkout being validated,
  so they could only report a stale field, never a substitution —
  `runtime_source` already commits to every byte of the tree.

  ### Migrating a `code_tribunal.release_inputs.v1` document

  | v1 | v2 |
  |---|---|
  | `hashes` | **delete** |
  | `schema_version: code_tribunal.release_inputs.v1` | `code_tribunal.release_inputs.v2` |

  Current tooling rejects v1 outright, naming this migration. The version is
  checked before the exact key set, so a v1 artifact is told it speaks a retired
  dialect rather than reported as carrying a stray key. Historical v1 inputs remain
  available as `release/release-inputs.json` in their release tags and are validated
  with the tooling that shipped beside them.

  Evidence-record freshness, waiver registration, and image-digest binding are
  unchanged.

### Changed

- **The published images run a curated packaged smoke suite instead of rerunning
  the checkout test suite (SPEC-58).** The base-image preflight used to bind-mount
  `ai-review/tests` over `/opt/ai-review/tests`, run `unittest discover` against
  it, and guard the result with a `MIN_EXECUTED_TESTS=400` floor. That coupled
  image publication to checkout test layout and proved nothing the checkout job had
  not already proved. Both the mount and the floor are gone.

  What replaces them is `ai_review_smoke`, a stdlib-only suite that ships in the
  image under a narrow `COPY` and is invoked **by module name** —
  `python -m ai_review_smoke base` on the base tag and
  `python -m ai_review_smoke reviewer` on the reviewer tag. Shipping it is a
  deliberate, bounded exception to "runtime images carry no product test code": it
  imports no pytest and no checkout test module, adds no dependency the runtime
  does not already install, and never runs during the build, so smoke test changes
  still do not alter image identity.

  The vacuous-pass property the floor was compensating for is preserved
  structurally rather than numerically. `COPY` fails at build time on a missing
  path, so a renamed or deleted suite fails the build; an absent module raises
  `ModuleNotFoundError` and exits non-zero; and the suite refuses to run unless the
  test IDs it loaded equal the manifest it declares, so a renamed method or a class
  that stopped subclassing `TestCase` fails naming the missing case instead of
  quietly reducing coverage. Test count remains forbidden as a quality signal.

  Two properties are genuine additions, not reorganizations: the critique stage,
  which no preflight exercised, and the cursor seat, which ships in the reviewer
  image but was absent from the `for reviewer in claude codex opencode` loop. The
  inline fixture-presence assertions and the `--help` module loop are absorbed into
  the suite's own manifests. `compileall`, the non-owner-uid ownership preflight,
  and the two OpenCode smoke scripts stay separate workflow steps. Packaged
  fixtures still ship at `/opt/ai-review/tests/fixtures`, which both preflights
  resolve with no mount.

- **`make test` fails with an actionable message when pytest is missing, and the
  `test-fallback` target is removed.** The fallback ran `unittest discover` over a
  suite that is substantially pytest-style bare functions, so it reported success
  over a silently collected subset. `make packaged-smoke` is the new explicit
  target for the packaged-runtime suite; pytest remains the documented local and CI
  test command.

- `critique_timeout_seconds` now defaults to a flat 900 seconds when a reviewer
  omits it. It previously fell back to that reviewer's `timeout_seconds` capped
  at 900, so a seat with `timeout_seconds: 1800` silently got 900 for critique
  while one with `600` silently got 600 — one field meaning two things. The
  shipped configuration states the value explicitly and is unaffected.

- Installed-workflow parity (`.github/workflows/ai-review.yml` against
  `ai-review/ci/review.github-actions.yml`) has **one implementation** in
  `release_common.sync_workflows`, with two callers. `make workflow-parity` is the
  repository gate and, via `make sync-workflows`, the repair command; the
  standalone release-manifest validator calls the same implementation
  independently, because it may run from a tagged worktree where `make quality`
  never did. Four separate copies of the byte comparison previously lived in
  `check_supply_chain_pins.py`, `check_release_inputs.py`, `test_ci_template.py`
  and the generator; none could repair what it reported, and the supply-chain copy
  ran inside the base image, where `.github/` does not exist — it guarded a file
  it could not see.

- **SPEC-21 is closed: Cursor is a supported peer reviewer seat.** The operator
  guides, `review.yaml`, the operations runbook, and the evidence index no longer
  describe it as experimental, as a "substitute" for another seat, or as blocked
  on an enablement queue, and the completed specification is deleted rather than
  kept in the open-specification index. Nothing about the shipped default changes
  — Cursor stays off in the default roster because enabling it is a deliberate
  second egress destination to Cursor's backend, not because acceptance was
  outstanding. Select it with `AI_REVIEW_REVIEWERS` and supply `CURSOR_API_KEY`.

- **`AI_REVIEW_CURSOR_MODEL: auto` is documented as what it is: valid.** `auto` is
  a Cursor CLI model selector that delegates model choice to Cursor, and both the
  configuration parser and the adapter have always accepted it. Documentation had
  called it a "discovery-only placeholder" that was "not valid Cursor-enablement
  evidence" — an evidence-campaign requirement stated as a product restriction.
  Pin an exact slug when you want model-stable reproducibility.
  Released records under `release/` and the historical evidence rows keep their
  wording, which describes what was true at those releases.

- `pipeline_trust.py` moved from the runtime package to `scripts/`, where
  `SECURITY_MODEL.md` already pointed readers. Nothing in the pipeline imported
  it — it audits a consumer's `.gitlab-ci.yml` — so it no longer ships inside
  the published images. `scripts/verify_pipeline_trust.py` is absorbed into it.

- Internal decomposition only, no behavior change (SPEC-39 milestone B). The three
  large orchestration modules were split along existing cohesive boundaries:
  `post.py` keeps only the CLI entry point, with command parsing, pure state
  planning (`state_plan.py`), mutation orchestration (`posting.py`), and summary
  rendering (`summary_render.py`) extracted out; `adapter_runner.py` separates
  output parsing/finalization from subprocess lifecycle; and `consensus.py`
  separates critique application (`critique.py`) from grouping. The shipped
  `python -m ai_review.post` / `.consensus` / `.adapter_runner` entry points,
  configuration keys, artifact schemas, and rendered output are unchanged — the
  golden consensus and post→gate end-to-end fixtures are byte-identical. Posting
  state transitions can now be tested without constructing a platform client, and
  an import-boundary test keeps the planning modules free of platform clients and
  `requests`.

## [1.0.2] - 2026-08-10

### Added

- All four reviewers are peer seats, selectable with one variable. `AI_REVIEW_REVIEWERS`
  takes a comma-separated roster (e.g. `AI_REVIEW_REVIEWERS=claude,codex,cursor`) that
  enables exactly the seats it names and disables the rest, so any of Claude, Codex,
  OpenCode, and Cursor may sit out — none is structurally fixed. Previously Cursor was a
  documented "substitute" for OpenCode, swapped by keeping two independent booleans in
  sync, which could silently produce a four-seat or two-seat panel. Unknown names,
  duplicates, and a single-seat roster are rejected at config load. The roster is part
  of the effective-config digest, so one scoped to only some pipeline jobs fails the
  cross-stage consistency check instead of producing a different panel per stage. The
  shipped default roster is unchanged (Claude, Codex, OpenCode); Cursor stays off by
  default for its separate egress destination, not because it ranks below the other seats.

### Changed

- Panel thresholds no longer have to be edited in lock-step with the reviewer set.
  `panel.min_successful_reviewers_for_blocking`, `…_for_resolution`, and
  `panel.quorum.votes_required` are now bounded by the *configured* reviewer count and
  take effect clamped to the *enabled* count. With the shipped values (`2`/`2`/`2`) this
  is a no-op at every supported panel size. Two consequences: a threshold above the
  configured count is now rejected as an authoring error where it previously only failed
  once the enabled count was too low, and clamping never drops a corroboration threshold
  below two — reducing the shipped configuration to a single enabled seat still fails
  loudly rather than self-approving. A configuration authored with one reviewer and
  matching thresholds of `1` remains valid.

- `AI_REVIEW_<REVIEWER>_ENABLED` is retired. `AI_REVIEW_REVIEWERS` is the sole
  environment-level panel selector; YAML `enabled` values remain the default when it is
  unset. The four old names are rejected with migration guidance rather than ignored,
  because repository and project variables outlive the templates that once read them.
  `CURSOR_API_KEY` is supplied only to the Cursor matrix entry — the workflow gates it
  on `matrix.reviewer == 'cursor'`, where it was previously placed in every reviewer job's
  environment whenever Cursor was enabled — and only when Cursor is on the roster.

  `AI_REVIEW_REVIEWERS` requires an image that ships roster support. An older pin ignores
  it and uses its packaged YAML defaults, so confirm both image pins before relying on a
  roster change.

### Fixed

- OpenCode structured batches no longer lose a finding when the schema transport
  returns an exact JSON-stringified object inside the `findings` or `critiques`
  array. The client decodes only one unambiguous object with duplicate-key
  rejection; prose, arrays, scalars, malformed JSON, and duplicate-key objects
  remain on the fail-closed path. This was found by the 1.0.2 real-provider release
  campaign and closed before the runtime and images were refrozen.

- The OpenCode reviewer can return a batch again. OpenCode injects a
  `StructuredOutput` tool when the session request carries
  `format: {"type":"json_schema", …}`, and its own prompt requires the model to call
  it — but the adapter's `"*": "deny"` permission wildcard covered that tool too, so
  it was filtered out of the tool list sent to the model. Every response was flagged
  `StructuredOutputError` and every review failed with zero findings. The tool is now
  allowed explicitly in both permission blocks; it returns the model's answer and has
  no filesystem or network reach, so the review boundary is unchanged. Verified in the
  built reviewer image against a loopback stub provider, with the adapter's own
  generated config and the sanitized review root: the tool is offered and the batch
  arrives through `info.structured`, and removing the rule reproduces the failure.

- An OpenCode server error is no longer unattributable. OpenCode answers an internal
  failure with `UnknownError`, "Check server logs for details", and a log `ref`, and
  writes the cause only to its own log file under `XDG_DATA_HOME` — which the adapter
  points into `out/.tmp`, so it was discarded with the run and the `ref` named a record
  nobody could read. The client now starts `serve` with `--print-logs --log-level INFO`
  and includes the captured server output in session and message request failures, so
  the cause travels with the error into the job log and the status artifact. The
  detail is read after the server is stopped and its log reader joined: OpenCode logs
  the ERROR line before it answers, and that log arrives on a pipe drained by another
  thread, so formatting it at the point of failure would race the reader and make the
  diagnosis depend on scheduling.

### Added

- A second OpenCode image preflight, `scripts/smoke_opencode_structured_output.sh`,
  runs the shipped adapter and client against a loopback stub provider inside the
  built reviewer image — the stub-provider follow-up SPEC-51 recorded as future work.
  It proves the reviewer is actually offered the `StructuredOutput` tool and that its
  batch survives the transport unchanged, and it carries its own negative control:
  removing only that permission must remove the tool and fail the run. It also closes
  the SPEC-51 canary gap that needed a live provider, forcing a real `grep` through
  the pinned ripgrep inside the sanitized review root and requiring a **non-empty**
  result, so a realpath-blinded reviewer cannot pass as error-free. Like the search
  probe it is a step in the image build job with no event condition, so it gates
  merge as well as publication, and it needs no provider secret.

### Changed

- The OpenCode reviewer now obtains its batch through OpenCode's structured-output
  transport: a pinned loopback `serve` client sends the stage schema as
  `format: {"type":"json_schema", …}` and emits the reviewer batch directly, so
  OpenCode no longer depends on the model volunteering a schema-conforming
  payload. See
  SPEC-50.

- Reviewer image pins are refreshed to OpenCode `1.18.12`, Claude Code
  `2.1.221`, Codex `0.146.0`, and Cursor Agent `2026.07.23-e383d2b` with its
  artifact SHA-256 recorded and verified against the published download.

### Security

- The OpenCode reviewer's filesystem reach is now bounded by its sanitized review
  root. OpenCode's `external_directory` permission is a key of its own, so the
  adapter's `"*": "deny"` tool wildcard never covered it and its default was
  `{"*": "ask"}` — in a headless reviewer, an approval request nobody can answer
  rather than a refusal. The adapter and the session client now deny it
  explicitly, and the image preflight reads the resolved permissions out of OpenCode's
  own resolver (`opencode --pure debug agent ai-reviewer`) against a config captured
  from a real adapter run, so a config that drifts or a default that changes fails the
  build. The preflight is a step in the image build job with no event condition, so it
  gates merge on pull requests as well as publication on main. `read`, `glob`, and
  `grep` remain allowed inside the root. See
  SPEC-51.

- The reviewer image ships a pinned, checksum-verified ripgrep on `PATH`, and a
  review-time ripgrep download now fails the review instead of producing postable
  findings — recognized as the server logs it, so the verdict does not depend on how
  much was logged afterwards. The diagnostic buffer stays bounded, which is why it
  cannot be what the check reads: a fetch early in a real review is evicted long
  before the session ends. OpenCode's `grep`/`glob` tools resolve `which("rg")` first and otherwise
  download ripgrep from GitHub releases at review time, verifying only that the
  response is non-empty; because the adapter gives each run a fresh `HOME`, that
  cache was always cold. No image previously installed ripgrep, so the tool had
  never worked — and a run with egress would have executed an unverified binary.
  `ripgrep.pin` records the extracted binary's digest as well as the tarball's, so
  what resolves on `PATH` is verified rather than only what was downloaded, and it
  names the `opencode-ai` version it belongs to so the two pins cannot drift apart.

- The OpenCode adapter resolves the pinned `opencode` and interpreter from
  `/usr/local/bin` before consulting the ambient `PATH`, and before the
  CLI-availability gate. Both are resolved before `env -i` and forwarded into it, so an
  ambient-first lookup let a binary earlier on the runner's `PATH` substitute itself
  for the pinned one — the substitution the fixed trusted `PATH` exists to prevent.
  Resolving by absolute path ahead of the gate also means a `PATH` without
  `/usr/local/bin` can no longer make the adapter reject a pinned binary that is
  present. When a pinned copy is expected but is missing from `/usr/local/bin` — evidenced
  by `/usr/local/lib/node_modules/opencode-ai` for `opencode` and by the packaged runtime
  install for the interpreter — the image is broken and the adapter now fails closed
  instead of running whatever is ambient. Both binaries carry that rule because both are
  forwarded into the fixed environment and executed there, so exempting one would move the
  substitution rather than prevent it. Where nothing was pinned there is nothing to prefer,
  so ambient resolution remains available for checkouts, dev machines, and the base image.

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
