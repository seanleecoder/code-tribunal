# SPEC-56 - Static first-party reviewer registry

- **Status:** Ready
- **Severity:** Medium consolidation and configuration-hardening
- **Effort:** M
- **Depends on:** SPEC-54/55. Appends to the `review_config.v3` removal list; opens no new
  config version
- **Contract changes:** `review_config.v3` removals; Claude's native Anthropic route is
  removed

## Objective

Create one trusted runtime authority for the four supported first-party reviewer seats and
remove immutable implementation wiring from operator configuration.

The four supported seats remain:

- `claude`;
- `codex`;
- `opencode`;
- `cursor`.

All four support both `review` and `critique`. Cursor is a supported production seat, off in
the shipped default only because it uses a distinct egress destination and credential.

## Why

The current reviewer configuration repeats implementation metadata that is not a deployment
choice:

- adapter script path;
- credential variable names;
- provider endpoint family;
- whether effort is supported;
- which `AI_REVIEW_REQUIRE_REAL_*` control applies.

Those values are also repeated across process-environment logic, workflow matrices, tests,
documentation, and image packaging. A configuration file can therefore appear to support a
custom adapter path or credential mapping even though the rest of the trusted runtime is
built around four known seats.

## Final decision

Add `ai-review/src/ai_review/reviewers.py` containing an explicit, immutable registry.

Conceptual shape:

```python
@dataclass(frozen=True)
class ReviewerDefinition:
    reviewer_id: str
    adapter_path: str
    credential_variables: frozenset[str]
    endpoint_kind: Literal[
        "anthropic_openrouter",
        "openrouter",
        "cursor_backend",
    ]
    require_real_control: str
    supports_effort: bool
    supported_stages: frozenset[Literal["review", "critique"]]
```

The registry must contain exactly four entries. It is trusted source code shipped in the
image, not project-controlled configuration.

`credential_variables` is a set rather than a single name because credentials are a property
of the seat's pinned route, and a scalar cannot describe what the runtime does today. Claude
currently receives two: its declared `ANTHROPIC_API_KEY`, plus `OPENROUTER_API_KEY` injected
conditionally in `adapter_process.py` when `ANTHROPIC_BASE_URL` names the OpenRouter route,
which `adapters/claude.sh` then remaps to `ANTHROPIC_AUTH_TOKEN` while blanking the first.
This spec removes that ambiguity rather than encoding it — see *Claude authenticates through
OpenRouter only*.

Three seats share `OPENROUTER_API_KEY` after that change: `claude`, `codex`, and `opencode`.
Sharing is correct — they share one provider account — so the registry must not assert that
credential names are unique across seats.

## Registry ownership

The registry owns:

- supported reviewer IDs;
- adapter relative paths;
- credential variable names;
- endpoint validation family;
- `REQUIRE_REAL` control name;
- effort support;
- supported stages.

The provider shell adapter owns:

- CLI flags;
- provider-specific config files;
- instruction-file stripping names that are genuinely provider-specific;
- structured-output invocation details;
- provider-specific binary resolution.

The common runner owns:

- process lifecycle and timeout;
- environment allowlisting;
- credential injection from the registry;
- prompt path and stage selection;
- output parsing and schema finalization;
- status/debug artifacts.

Do not place CLI command construction in the registry. Do not create a generic provider
capability framework.

## Configuration contract

In `review_config.v3`, a reviewer entry contains only deployment choices.

Required:

- `enabled`;
- `model`;
- `timeout_seconds`;
- `max_findings`.

Optional:

- `effort` when the registry says the seat supports it;
- `critique_timeout_seconds`.

Remove:

- `adapter`;
- `credential_variable`.

### The reviewer key set is exactly the registry ID set

Reviewer keys must equal the registry ID set — all four seats, always present. A missing seat
is rejected at config load, as is an unknown reviewer ID. `enabled` selects the roster; the
key set is not how a deployment chooses reviewers.

A subset would be unimplementable against the static four-seat workflows. Both platforms
always dispatch all four jobs, and the two omissions behave differently today:

- a seat present with `enabled: false` writes a deterministic `skipped` batch and status and
  exits `0`, which consensus explicitly accepts;
- a seat absent from `reviewers` entirely raises `unknown reviewer` in the adapter runner,
  which becomes a `config_error` batch and a non-zero exit, and consensus then fails the whole
  run with an integrity error rather than tolerating it.

So a config that merely omits a seat makes every run fail at consensus. Requiring the full set
keeps the one working mechanism — `enabled` — as the only roster control. The existing floor
of three enabled seats continues to bound how thin a roster may get.

Delete `resolve_adapter_path()` and any configuration-path traversal behavior that existed
only to support configured adapter paths.

### This does not open a new configuration version

SPEC-56 appends `adapter` and `credential_variable` to the existing `review_config.v3` removal
list. It does **not** open `review_config.v4`.

SPEC-54 states that SPEC-54 through SPEC-57 jointly define `review_config.v3`, and the series
README instructs SPEC-57 — the other config-key-removing spec — to append to that same removal
list rather than open a new config version. SPEC-56 is the same operation on the same
in-progress contract. Opening v4 here would force either a v5 at SPEC-57 or an inconsistent
rule between two sibling specs.

The implementation must therefore:

- add both keys to the removed-key tuple in `config.py`, so the existing `review_config.v2`
  rejection message names them. A test already asserts that message names every key removed
  between v2 and v3, so an appended removal without an appended message line fails;
- extend the v2-to-v3 migration table in `CHANGELOG.md` with both keys. SPEC-57, as the last
  config-changing spec in the series, owns the final consolidated message text.

## Runtime changes

### Adapter runner

- Resolve the reviewer definition before reading reviewer-specific configuration.
- Obtain the adapter path from the registry under the trusted runtime root.
- Reject unsupported stages through the registry.
- Continue to emit a deterministic skipped artifact for a configured but disabled seat.

### The trusted adapter root

Deleting `resolve_adapter_path()` removes the only thing that turned a relative adapter value
into a path, so this spec must name the replacement authority or the gap moves rather than
closes.

**The trusted root is derived from the installed package**, not from an environment variable.
The image already fixes the layout: adapters, config, and source are copied to known absolute
locations, and the runtime imports `ai_review` from that same tree. Deriving the root from the
installed package location means the authority is the image, which is what "trusted runtime
root" was always meant to denote.

Rules:

- registry `adapter_path` values stay relative to that root and are never absolute;
- a resolved path is normalized and must be contained by the root. Containment is checked
  after normalization, so `..` segments cannot escape;
- resolution never consults configuration, the working directory, or `AI_REVIEW_CONFIG`.

Note that today's `resolve_adapter_path()` performs no containment check at all and passes
absolute values through unchanged, so a config-supplied `adapter` resolves anywhere on the
filesystem. Removing configured adapter paths closes a live traversal gap; the containment
rule above ensures the registry does not reintroduce one.

The executable-bit check belongs to the image build, not to the request path. A per-run
`os.access()` on every adapter would be a runtime cost for a property that cannot change after
the image is sealed. The test named below asserts it against the built image.

`AI_REVIEW_TRUSTED_ROOT` is left with no consumer by this change. It is declared only in the
GitLab template and read by no runtime code. Retire it from the template and from the
configuration reference, or state plainly that it is documentation-only; do not leave a
documented variable that nothing reads.

### Claude authenticates through OpenRouter only

The native Anthropic route is removed. Claude declares `OPENROUTER_API_KEY` and
`endpoint_kind="anthropic_openrouter"`, and `ANTHROPIC_BASE_URL` must equal the pinned
OpenRouter endpoint rather than being optional.

This is a behavior removal, not a refactor. It is in scope because a seat with two
conditionally-live credentials cannot be described by a registry entry, and because both
shipped workflows already pin the OpenRouter route — the native path is supported but
unexercised. The implementation must:

- drop the claude-specific conditional `OPENROUTER_API_KEY` injection in `adapter_process.py`;
  the seat's declared credential now covers it;
- tighten claude endpoint validation from "unset or the OpenRouter endpoint" to "must be the
  OpenRouter endpoint". An unset value becomes an error;

  **As implemented, superseded — the endpoint is injected, not validated.** Requiring the
  caller to supply a value with exactly one accepted setting broke two callers this document
  did not enumerate: the reviewer-image publication preflight
  (`.github/workflows/publish-ai-review-images.yml`), which loops over claude/codex/opencode
  under `sh -e`, and `make review-local`, whose default reviewer is claude — neither exports
  a provider endpoint, and the check runs before the adapter is spawned, so even the mock
  path was unreachable. `_build_adapter_env()` therefore sets the endpoint from the seat's
  `endpoint_kind` and no longer reads the ambient value. This is strictly stronger than the
  specified check: an unset value cannot fail, and a lookalike host cannot be supplied at
  all. Ambient `ANTHROPIC_BASE_URL` / `OPENROUTER_BASE_URL` are overridden rather than
  rejected, because they are provider-native names an unrelated developer or CI shell may
  legitimately export. The redundant re-checks in `adapters/codex.sh` and
  `adapters/opencode.sh` are deleted with the validation, and both CI templates stop
  declaring variables that nothing reads;
- make the `OPENROUTER_API_KEY` to `ANTHROPIC_AUTH_TOKEN` mapping in `adapters/claude.sh`
  unconditional, and drop the line that blanks `ANTHROPIC_API_KEY`;
- record the removal in `CHANGELOG.md` and update the credential table in the configuration
  reference. An operator running Claude against Anthropic directly must be told the route is
  gone and how to move to OpenRouter.

### Process environment

- `_build_adapter_env()` obtains the credential set and endpoint family from the registry.
- Only the selected reviewer's declared credentials are copied, and no seat receives a
  credential it does not declare.
- Cursor receives no OpenRouter fallback credential.
- The provider endpoint is injected per `endpoint_kind` — one accepted host per family,
  supplied by the runner — rather than forwarded from the environment and validated against
  string sets duplicated in `adapter_process.py`.
- Only the seat's declared `require_real_control` is forwarded, so each adapter reads exactly
  that name. A second name read as a fallback is either dead or a fail-closed guard the
  runner no longer delivers.

Note that this is the only place per-seat credential isolation is enforced on GitLab, whose
template injects no credentials per job — project variables reach every job in the pipeline.
GitHub additionally gates `CURSOR_API_KEY` on the matrix entry, but passes
`OPENROUTER_API_KEY` to all four. Workflow-level routing is therefore not a second isolation
boundary, and tests must not treat it as one.

### Effort

- Validation checks `supports_effort` from the registry.
- Cursor continues to reject a separate effort value because reasoning depth is encoded in
  its model selector.

## Workflow and documentation consistency

Keep workflow matrices explicit and readable. Do not generate the entire YAML workflow from
Python.

Add one narrow contract test that parses the workflows and the shipped config and checks them
against the registry. It must assert the **dispatch mapping**, not only the seat-name sets:

- the GitHub review and critique matrices equal the registry reviewer ID set;
- the shipped config reviewer keys equal the registry reviewer ID set;
- for every GitLab review and critique job: the job name, its `stage`, and its
  `variables.REVIEWER` agree, and the review and critique `needs:` sets are complete.

The GitLab half is the part a name-set comparison cannot cover. Reviewer identity there is
carried by the `REVIEWER` variable and the bracketed job name is a display string that nothing
derives from — a job named for one seat can dispatch another, and the seat determines which
credential the adapter receives. A set comparison over job names passes on that mismatch.

That mapping is currently guarded by eight separately-written per-job assertions in the CI
template tests. Those assertions are the scattered duplication this spec targets, but they are
also a real security-relevant guard, so the new test must **subsume** them before they are
deleted. Deriving the expectations from the registry, rather than restating literals, is what
makes one test able to replace eight. Once it does, it must be the only such parity test.

Use the registry to parameterize shared adapter contract tests.

Documentation continues to be written for humans. Do not build a general documentation
generator. Update the configuration reference to state that adapter paths and credential
names are fixed by the trusted image.

## Cursor trust boundary

This spec formalizes Cursor CLI as a trusted pinned third-party dependency.

Code Tribunal tests what it controls:

- the pinned binary is installed and selected;
- `--mode ask`, the configured permission file, and disposable HOME/workspace are supplied;
- only `CURSOR_API_KEY` reaches the Cursor adapter;
- platform and OpenRouter credentials do not reach Cursor;
- the snapshot is sanitized;
- both review and critique produce valid finalized artifacts.

Code Tribunal does not continuously prove that Cursor internally enforces its permission
configuration. Do not restore the deleted large behavioral permission smoke as a normal
quality or release gate. SPEC-61 may run a functional Cursor review/critique canary, but not
a third-party permission conformance suite.

Document the residual trust plainly.

## Tests

Add or consolidate:

- registry contains exactly the four supported IDs;
- every registry adapter path is relative, normalizes to a path contained by the trusted root,
  and exists and is executable in the built image;
- a registry path containing `..` or an absolute path is rejected;
- the credential set `_build_adapter_env()` copies for a seat equals that seat's registry
  `credential_variables`, and no seat receives a credential it does not declare. These are the
  two assertions that matter; do not assert that credential names are unique across seats,
  because three seats legitimately share the OpenRouter account;
- the endpoint `_build_adapter_env()` sets for a seat is the one its `endpoint_kind` implies,
  from a clean environment and with a hostile lookalike exported — Claude gets
  `ANTHROPIC_BASE_URL`, Codex and OpenCode get `OPENROUTER_BASE_URL`, Cursor gets neither
  (replaces "Claude's endpoint validation rejects an unset `ANTHROPIC_BASE_URL`", which
  described the superseded contract above);
- every seat produces a valid batch on the mock path from a clean shell, with no credential,
  `REQUIRE_REAL` control, or provider endpoint exported. The claude case is the one the
  original series omitted, and its absence is why the preflight and local-harness breaks
  shipped green;
- each adapter's `REQUIRE_REAL` assignment names exactly its registry `require_real_control`
  and no other;
- review and critique are supported for every seat;
- Cursor is the only seat without effort support;
- config rejects `adapter` and `credential_variable` keys;
- config rejects unknown reviewer IDs;
- config rejects a document that omits any registry seat, at load time rather than at
  consensus time;
- adapter runner uses the registry path, not a config path;
- credential isolation tests are parameterized over all four seats;
- the GitLab job-name to `stage` to `REVIEWER` mapping and the `needs:` sets match the
  registry, replacing the per-job literal assertions in the CI template tests;
- GitHub matrices and the shipped config seat set match the registry.

One reviewer ID list may exist in Python source. The reserved GitLab job names in the pipeline
trust auditor are exempt from that prohibition: they are a projection of the external pipeline
contract, not a reviewer roster. The set deliberately contains names that no registry can
produce — shared job templates, the non-reviewer pipeline stages, and at least one job already
deleted from the shipped template but kept reserved for consumers pinned to an older version.
Its test asserts a superset relation rather than equality for exactly that reason, and
un-reserving a name loosens a trust boundary against third-party configuration. If the eight
bracketed seat names are derived from the registry, they must be unioned with the static and
retired names and the superset property kept under test; deriving the whole set is wrong.

## Acceptance criteria

- Operator config contains no executable path or credential-variable wiring.
- Four first-party adapters remain independently selectable, and the config lists all four.
- One registry is the runtime authority for reviewer identity and immutable metadata.
- The trusted adapter root is derived from the installed package, and every registry path
  resolves under it after normalization.
- Provider-specific CLI behavior remains in provider adapters.
- Workflow and config seat parity is checked once, and that one check covers the GitLab
  job-to-`REVIEWER` mapping rather than job names alone.
- Claude authenticates through OpenRouter only, and the removal is in `CHANGELOG.md`.
- No new configuration version is opened.
- Cursor remains supported and its trust boundary is documented without a large runtime
  permission test.
- `make quality` passes.

## Non-goals

- Do not add third-party adapter discovery.
- Do not add entry points, dynamic imports, or project-supplied executable paths.
- Do not reduce the number of first-party adapters.
- Do not combine provider shell scripts into one branch-heavy script.
- Do not move provider CLI flags into the registry.
