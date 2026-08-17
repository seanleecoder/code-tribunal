# SPEC-58 - Contract-oriented test consolidation

- **Status:** Ready after SPEC-54 through SPEC-57
- **Severity:** Medium maintainability improvement
- **Effort:** L
- **Behavior:** Must remain unchanged except where prior specs explicitly changed it

## Objective

Reduce test maintenance by organizing coverage around stable product contracts rather than
private helper seams, duplicated provider cases, workflow prose, and a full unit-suite rerun
inside packaged images.

This spec must delete replaced tests. It is not complete if it only adds a new contract suite
beside the old tests.

## Protected coverage

Do not reduce coverage for:

- revision binding and stale-head protection;
- descriptor-relative no-follow snapshot traversal;
- symlink and special-file rejection;
- external-fork secret refusal;
- provider credential isolation and endpoint validation;
- adapter process-group termination on timeout;
- model-output and artifact schema validation;
- duplicate JSON-key rejection;
- deterministic grouping, critique application, and support decisions;
- state matching, retention, commands, and thread reconciliation;
- literal rendering and secret redaction;
- immutable image/action/package pins;
- release evidence freshness.

## Target test architecture

### 1. Reviewer adapter contract

Create one parameterized contract suite using the SPEC-56 registry.

Run each applicable case across all four adapters and both stages:

- only the selected credential is forwarded;
- platform tokens and unrelated provider credentials are absent;
- model IDs are validated before process spawn;
- endpoint rules are enforced;
- disabled seats produce skipped artifacts without spawning a CLI;
- review and critique prompts resolve correctly;
- local mock requires both authorization gates;
- timeout kills the process group and records partial output;
- malformed output produces the expected finalized error artifact;
- valid output produces schema-valid finding or critique artifacts;
- snapshot instruction files are stripped according to the provider adapter.

Keep provider-specific tests only for behavior that differs:

- Claude Anthropic-compatible OpenRouter routing;
- Codex config/effort flags;
- OpenCode server/client and trusted ripgrep behavior;
- Cursor ask-mode invocation, dedicated credential, and disposable HOME.

Delete repeated per-provider tests for common runner behavior after the contract suite covers
them.

### 2. Reducer policy tables

Create compact table-driven tests for:

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

Create one behavior contract executed against GitHub and GitLab fakes:

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
`MIN_EXECUTED_TESTS` floor with a curated standard-library packaged-runtime smoke suite.

The packaged suite should verify only properties that require the built image:

- expected runtime files exist and test source does not ship in the final image;
- all runtime modules import;
- schemas load;
- default config loads;
- each adapter script is executable;
- local mock review and critique complete inside the image;
- one local consensus run validates;
- read-only container execution works;
- pinned OpenCode/ripgrep preflight remains separate and runs where required.

Do not use test count as a quality signal.

After the packaged suite lands:

- remove the generic `test-fallback` Make target from contributor workflow;
- keep one explicit `packaged-smoke` target for the image workflow;
- keep pytest as the documented local and CI test command.

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

1. Add contract fixtures and parameterized suites.
2. Demonstrate equivalent coverage with negative tests that fail when the protected behavior
   is removed.
3. Delete the replaced provider, fake, helper-seam, and image-suite tests in the same pull
   request or immediate follow-up.
4. Remove dead support builders after all users migrate.
5. Record any intentionally removed assertion in the pull request's complexity delta.

## Acceptance criteria

- Common adapter behavior has one parameterized suite.
- Consensus support policy has one table-driven suite.
- GitHub and GitLab share one platform contract suite.
- The packaged image runs a curated smoke suite, not the full checkout suite.
- `MIN_EXECUTED_TESTS` and contributor `test-fallback` are removed.
- Replaced tests and support code are deleted.
- No protected security or release property loses a negative test.
- Test names describe product behavior rather than old spec phases.
- `make quality` and the image build/preflight workflow pass.

## Non-goals

- Do not pursue a target test count or coverage percentage reduction.
- Do not combine all tests into one file.
- Do not replace readable fakes with a general mocking framework.
- Do not remove provider-specific security tests that protect real differences.
