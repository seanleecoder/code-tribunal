# SPEC-58 - Contract-oriented test consolidation

- **Status:** Ready
- **Severity:** Medium maintainability improvement
- **Effort:** L
- **Depends on:** Implemented SPEC-54 through SPEC-57. `consensus.v2` and `review_config.v3`
  are closed, so the production seams this spec reorganizes tests around have stopped moving.
- **Behavior:** Must remain unchanged except where prior specs explicitly changed it

## Objective

Reduce test maintenance by organizing coverage around stable product contracts rather than
private helper seams, duplicated provider cases, workflow prose, and a full unit-suite rerun
inside packaged images.

This spec must delete replaced tests. It is not complete if it only adds a new contract suite
beside the old tests.

## Why

Four kinds of duplication accumulated while the production seams were moving:

- per-provider copies of behavior that `adapter_runner.py` implements once for every seat;
- reducer tests that call several private helpers to reconstruct what `build_consensus()`
  already returns;
- two platform fakes with parallel method-level tests where the product behavior is identical;
- an image preflight that re-runs the entire checkout suite against a bind mount, which
  couples image publication to checkout test layout and proves nothing the checkout job did
  not already prove.

Each of these makes an ordinary production change touch many tests without adding signal. The
target architecture keeps one suite per contract and one axis per property.

## Protected coverage

Do not reduce coverage for:

- revision binding and stale-head protection;
- descriptor-relative no-follow snapshot traversal;
- symlink and special-file rejection;
- external-fork secret refusal;
- provider credential isolation and endpoint validation;
- pinned-CLI runtime resolution: fixed trusted `PATH` order, preferring a pinned binary over a
  shadowing decoy, refusing an ambient CLI when a pinned one was installed, and never
  executing an ambient interpreter when a pinned one exists
  (`unit/test_openrouter_adapters.py`, roughly lines 232-525);
- adapter process-group termination on timeout;
- model-output and artifact schema validation;
- duplicate JSON-key rejection;
- HTTP retry and backoff behavior (`unit/test_http_retry.py`);
- module import-layering boundaries (`unit/test_import_boundaries.py`);
- per-reviewer finding caps (`unit/test_finding_cap.py`);
- canonical hash determinism for body, context, and state hashes
  (`unit/test_body_hash.py`, `unit/test_context_hash.py`, `unit/test_state_hash.py`), which
  thread identity and update detection depend on;
- deterministic grouping, critique application, and support decisions;
- state matching, retention, commands, and thread reconciliation;
- literal rendering and secret redaction;
- immutable image/action/package pins;
- release evidence freshness.

"Immutable image/action/package pins" covers supply-chain pin bytes only. Runtime binary
resolution is the separate pinned-CLI item above; consolidating per-provider adapter tests must
not take it.

## Target test architecture

### 1. Reviewer adapter contract

Extend the existing SPEC-56 registry-driven suite (`unit/test_reviewers.py`) and its fixtures.
Do not create a third suite beside it and `unit/test_openrouter_adapters.py`.

Choose the axis per property. Parameterizing shared-runner behavior over seats and stages
multiplies runtime without adding signal, because `adapter_runner.py` implements it once.

Run once against the shared runner, with one representative seat and stage. These are
seat- and stage-independent code paths in `run_adapter()` and `adapter_process.py`:

- local mock requires both authorization gates — `_local_mock_unauthorized()` reads the
  environment only and is reached before any seat lookup;
- model IDs are validated before process spawn — `_model_id_validation_error()` is a pure
  format check on the resolved model string;
- timeout kills the process group and records partial output;
- malformed output produces the expected finalized error artifact;
- valid output produces schema-valid finding or critique artifacts.

Run per seat, not per stage. Each of these is a field of the seat's `ReviewerDefinition`, and
stage reaches `adapter_process.py` only as `AI_REVIEW_STAGE` and the timeout budget:

- only the selected credential is forwarded (`credential_variables`);
- platform tokens and unrelated provider credentials are absent;
- endpoint rules are enforced (`endpoint_kind`);
- snapshot instruction files are stripped according to the provider adapter.

Run per stage, not per seat:

- review and critique prompts resolve correctly;
- the stage budget resolves from the correct key — `resolve_reviewer_timeout_seconds()` reads
  `timeout_seconds` for review and `critique_timeout_seconds` for critique, with distinct
  fallback behavior.

Run per seat and per stage, because both axes are load-bearing:

- disabled seats produce skipped artifacts without spawning a CLI. The `enabled` key is
  per seat, and `_output_file(stage, reviewer)` puts the skipped artifact on a
  stage-dependent path with a stage-dependent schema.

Stage support refusal needs one registry-level case, not a matrix: `supported_stages` is
`_ALL_STAGES` for all four seats today, so the refusal path in `run_adapter()` is unreachable
per seat and must be driven from the registry.

Contract fixtures must be built through `tests/support/config_yaml.runtime_config`, per
SPEC-57, not from partial dictionaries. Two landed constraints follow from that:

- `config.py` enforces a floor of three enabled seats (`_MINIMUM_PANEL_REVIEWERS`), so the
  "disabled seat" cases need a roster that keeps at least three seats enabled while the seat
  under test is off;
- the shipped default disables cursor, so cursor's enabled-seat cases need an explicit roster.

Keep provider-specific tests only for behavior that differs:

- Claude Anthropic-compatible OpenRouter routing;
- Codex config/effort flags;
- OpenCode server/client and trusted ripgrep behavior;
- Cursor ask-mode invocation, dedicated credential, and disposable HOME.

Delete repeated per-provider tests for common runner behavior after the contract suite covers
them.

### 2. Reducer policy tables

SPEC-55 already collapsed the duplicated reducer-policy tests this section originally named.
Re-survey `unit/test_consensus_policy.py`, `unit/test_panel_degradation.py`,
`unit/test_grouping.py`, and `unit/test_phase5_consensus.py` before assuming duplication
remains; where it does not, this section is satisfied and nothing is deleted for it.

Where duplication remains, maintain compact table-driven tests for:

- panel full/degraded/failed status;
- independent support decisions from SPEC-54;
- duplicate grouping;
- majority-noise suppression;
- severity adjustment boundaries;
- deterministic ordering and identity;
- malformed or mixed-run artifacts.

Avoid tests that call multiple private reducer helpers only to reconstruct the public
`build_consensus()` result.

### 3. Platform contract

Extend the existing behavior contract in `tests/contract/test_review_platform.py`, which
already runs a protocol and human-command contract against both fakes. Do not create a second
platform contract suite.

The contract must cover:

- fetch version, diff, and current head;
- build single- and multiline positions;
- create and update a thread;
- resolve and reopen a thread;
- create and update the owned state/summary note;
- map platform failures to `ReviewPlatformError`;
- preserve author identity needed for state and command authorization.

Keep transport-specific payload tests in each platform module. Delete duplicate fake methods
and alias tests that do not express a product difference.

### 4. Posting lifecycle scenarios

Maintain one end-to-end fake-platform suite for:

1. no findings;
2. supported inline finding;
3. FYI-only run;
4. unanchorable summary fallback;
5. unchanged rerun;
6. updated finding;
7. absent finding resolved with quorum;
8. missing resolution quorum;
9. human wontfix and reopen;
10. stale head;
11. state overflow;
12. platform mutation failure.

Prefer assertions on `post_result`, persisted state, and fake-platform calls. Avoid asserting
private helper names or internal mutation order unless that order is the public behavior.

### 5. Packaged-runtime smoke suite

The checkout pytest suite is the authoritative product test suite.

Replace the image-time bind-mounted full `unittest discover` run and its numeric
`MIN_EXECUTED_TESTS` floor with a curated packaged-runtime smoke suite that ships in the
image.

#### What ships and what does not

Three distinct things, which the earlier wording conflated:

- the checkout suite's `test_*.py` modules must not ship in the final image — that rerun is
  what this section removes;
- `ai-review/tests/fixtures` **does** ship and must keep shipping. `base.Dockerfile` copies it
  to `/opt/ai-review/tests/fixtures`, and both preflights read those exact paths with no mount:
  the base preflight asserts the image's own fixtures, and `run_reviewer.sh` resolves `--diff`
  and `--repo` from them. Removing that `COPY` breaks the reviewer preflight;
- the packaged smoke suite itself ships, as an importable module under a dedicated packaged
  path. It is a runtime artifact of the image, not a copy of the checkout suite.

Shipping the smoke suite is a deliberate, narrow exception to the "runtime images carry no test
code" contract, which exists so a production image processing untrusted diffs and model output
carries no test code. The exception is justified only under these limits, and the `COPY` must be
narrow enough that a revert to copying the whole tree still fails:

- self-contained standard-library code with no pytest, no network, and no execution surface the
  runtime does not already have;
- it is what restores the build-time `COPY` guarantee that the removed count floor was
  compensating for;
- it never runs during the image build, only at preflight, so test-code changes still do not
  couple to image identity.

#### Replacing the vacuous-pass guard

The floor being removed exists for one specific failure: `docker run -v` silently creates an
empty directory when the host path is missing or renamed, and `unittest discover` exits 0 on
zero collection, so a preflight could report success having verified nothing and still publish
an image. Do not delete the floor without replacing that property.

Shipping the suite restores it structurally, and the replacement must be structural, not a
count:

- `COPY` fails at build time on a missing path, so a renamed or deleted suite fails the build
  rather than passing vacuously;
- the preflight invokes the suite **by module name** (`python -m <packaged suite module>`), not
  by discovery against a mount, so an absent suite raises `ModuleNotFoundError` and exits
  non-zero;
- the suite declares an explicit manifest of the test IDs it must contain, builds its
  `unittest.TestSuite` by naming those cases directly rather than by discovery, and fails when
  the loaded ID set does not equal the manifest.

The manifest is required, not a sentinel case. A sentinel proves that *something* ran; it
cannot detect a case that stopped matching collection — a renamed method, a class that no
longer subclasses `TestCase`, a decorator that swallowed it. In that state a sentinel-guarded
module still exits 0 and publishes an image that silently lost a packaged-runtime check.
Explicit construction turns a renamed class into an import or attribute error, and the ID-set
comparison turns a renamed or dropped method into a failure naming the missing case.

The manifest is an identity check, not a count: it must compare the set of expected IDs, and
adding or removing a smoke case must require editing the manifest in the same change. Test
count remains forbidden as a quality signal.

#### Which image runs which properties

The properties split across the two tags; a single suite run against one tag cannot cover both.

Base tag (`AI_REVIEW_BASE_TAG`):

- expected runtime files exist;
- packaged fixtures exist at the paths the reviewer preflight resolves;
- all runtime modules import;
- schemas load;
- default config loads;
- read-only container execution with a `tmpfs /tmp` works;
- the existing `compileall` and non-owner-uid ownership checks.

Reviewer tag (`AI_REVIEW_REVIEWER_TAG`) — these need the pinned CLIs, which only the reviewer
image has:

- each adapter script is executable;
- pinned CLIs report a version;
- local mock review and critique complete inside the image for all four seats;
- one local consensus run validates against `consensus.schema.json`, and each seat's batch
  against `finding_batch.schema.json`.

#### Relationship to the existing preflight steps

Most of the curated list already exists as inline shell in
`.github/workflows/publish-ai-review-images.yml`. This section reorganizes those steps; it does
not add a parallel suite beside them.

Absorbed into the packaged suite (delete the inline shell):

- the base-image fixture-presence assertions
  (`test -f /opt/ai-review/tests/fixtures/diffs/simple.diff` and the `repos/simple` check);
- the `for module in input_bundle consensus post schema` `--help` loop, which is the inline
  version of "all runtime modules import". The packaged suite must enumerate the runtime
  modules in its own manifest rather than in workflow shell;
- the reviewer-image mock review, schema validation, and consensus run.

Kept as separate workflow steps:

- `compileall` and the non-owner-uid ownership preflight, which assert container-level
  properties rather than in-image behavior;
- `scripts/smoke_opencode_search_tools.sh` and
  `scripts/smoke_opencode_structured_output.sh`. They carry negative controls and their own
  harness, and must keep gating before the image artifact is saved.

Two properties in the curated list are genuine additions, not reorganizations:

- the critique stage, which no preflight currently exercises;
- the cursor seat, which is in the image but absent from the current
  `for reviewer in claude codex opencode` loop.

#### Standard-library constraint

The packaged suite must be self-contained `unittest.TestCase` code with no pytest dependency
and no import of checkout test modules. A large part of the checkout suite is pytest-style bare
functions (`tests/contract/test_review_platform.py`, `tests/integration/*`) that
`unittest discover` cannot collect — already documented in
`unit/test_verify_pipeline_trust.py`. Reusing checkout files would silently collect a subset.

#### Contributor workflow

After the packaged suite lands:

- keep one explicit `packaged-smoke` Make target for the image workflow;
- keep pytest as the documented local and CI test command;
- remove the generic `test-fallback` Make target. `make test` currently dispatches to it when
  pytest is unimportable, so removing the target alone would make `make test` fail with a Make
  error. Replace the dispatch with an actionable failure naming `requirements-dev.txt`, and
  update `docs/development/setup.md`, which documents `make test`.

## Private-seam cleanup

When a production refactor changes an internal helper:

- migrate tests to the owning public function or pure module contract;
- delete compatibility wrappers used only by tests;
- do not preserve private import paths.

Tests may directly exercise pure algorithms such as state matching, anchor remapping, and
literal rendering. The rule is not "never test private functions"; it is "do not make
orchestration internals a compatibility surface."

## Documentation tests

Keep:

- current-document link and heading validation;
- active config/environment inventory;
- trusted pipeline example validation;
- released-note byte identity;
- release evidence and pin checks.

Do not add or restore:

- README line limits;
- directory-index shape rules;
- exact phrase blocklists;
- exact prose placement;
- tests of GNU Make behavior;
- duplicate workflow parity checks.

## Migration plan

1. Add contract fixtures and parameterized suites, extending the suites named in sections 1-3
   rather than creating new ones.
2. Record an explicit protected-item to test mapping in the pull request: for each entry under
   "Protected coverage", the test that still fails when the behavior is removed. Without that
   mapping the equivalence claim and the acceptance criterion below are unverifiable.
3. Rewrite the executable tests that pin today's image-preflight arrangement, in the same
   change that removes it. Every one of these asserts a literal command that this spec deletes,
   so until they are rewritten the `MIN_EXECUTED_TESTS`-removal and `make quality` acceptance
   criteria are mutually unsatisfiable. Each must assert the *replacement* property, not simply
   drop:
   - `test_distribution_contract.test_ci_stages_the_suite_into_the_images_own_tests_path` —
     pins `python -m unittest discover -s /opt/ai-review/tests` and the checkout bind mount.
     Both go away, so this becomes the by-name invocation of the packaged suite with no mount;
     the method name describes the staging that no longer happens and should be renamed.
   - `test_distribution_contract.test_preflight_requires_the_mounted_suite_to_actually_run` —
     pins `MIN_EXECUTED_TESTS` and `executed=$((ran - skipped))`. Must become the manifest
     guard: the suite's loaded test-ID set equals its declared manifest, plus the build-time
     `COPY` that makes a renamed suite fail the build.
   - `test_distribution_contract.test_preflights_verify_the_images_own_fixtures_before_overlaying`
     — pins `test -f /opt/ai-review/tests/fixtures/diffs/simple.diff` and the `repos/simple`
     check. The overlay it guards against no longer exists once the mount is gone, but the
     property does: the fixtures must still ship and the reviewer preflight still resolves them
     with no mount. Must assert that the packaged suite verifies them.
   - `test_distribution_contract.test_base_image_smoke_loop_names_only_modules_that_exist` —
     parses `for module in ([^;]+); do` out of the workflow and checks each named module
     exists. Must assert the same property against the packaged suite's runtime-module
     manifest.
   - `test_ci_template` build-preflight substring loop — asserts `python -m unittest discover`,
     `AI_REVIEW_LOCAL_MOCK=1`, `run_reviewer.sh "$reviewer" review`, and `consensus.schema.json`
     appear in the build-preflight job and not in publish. Four of those five strings move into
     the packaged suite, so the loop must name the packaged-suite invocation instead while
     keeping the "not in publish" half intact.
   - `test_distribution_contract.test_container_ships_fixtures_but_no_test_code` — asserts the
     fixtures `COPY`, the absence of a broad `COPY ai-review/tests /opt/ai-review/tests`, and
     the absence of `unittest discover` in the Dockerfile. A narrow `COPY` of the packaged
     suite alone does not trip any of those literal assertions, but it does cut against the
     rationale the docstring states — that a production image processing untrusted diffs and
     model output carries no test code. Resolve it explicitly rather than by passing on a
     string mismatch: tighten the test to allow exactly the packaged-suite path and keep
     forbidding the broad copy. The `unittest discover`-in-Dockerfile prohibition stays; this
     spec runs the packaged suite at preflight, never during the build.
   Survey for further pins before implementing: this list is what the current arrangement pins,
   not a guarantee that nothing else does.
4. Delete the replaced provider, fake, helper-seam, and image-suite tests in the same pull
   request or immediate follow-up.
5. Remove dead support builders after all users migrate.
6. Record any intentionally removed assertion in the pull request's complexity delta.

## Acceptance criteria

- Common adapter behavior has one parameterized suite, with the axis stated per property.
- Consensus support policy has one table-driven suite.
- GitHub and GitLab share one platform contract suite.
- The packaged image runs a curated smoke suite, not the full checkout suite, split across the
  base and reviewer tags.
- The vacuous-pass property the floor guarded is preserved structurally: a renamed or missing
  suite fails the build or exits non-zero, and the suite's loaded test-ID set equals its
  declared manifest.
- `MIN_EXECUTED_TESTS` and contributor `test-fallback` are removed, and the three tests that
  pinned them assert the replacement arrangement.
- `make test` fails with an actionable message when pytest is unavailable.
- Packaged fixtures still ship and both preflights still resolve them from the image.
- Replaced tests and support code are deleted.
- No protected security or release property loses a negative test, demonstrated by the
  protected-item to test mapping.
- Test names describe product behavior rather than old spec phases.
- `make quality` and the image build/preflight workflow pass.

## Non-goals

- Do not pursue a target test count or coverage percentage reduction.
- Do not combine all tests into one file.
- Do not replace readable fakes with a general mocking framework.
- Do not remove provider-specific security tests that protect real differences.
- Do not parameterize shared-runner behavior across seats or stages.
