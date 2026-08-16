# SPEC-56 - Static first-party reviewer registry

- **Status:** Ready
- **Severity:** Medium consolidation and configuration-hardening
- **Effort:** M
- **Depends on:** Coordinate the `review_config.v3` migration with SPEC-54 and SPEC-55

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
- credential variable name;
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
    credential_variable: str
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

Reviewer keys must be a non-empty subset of the registry IDs. Unknown reviewer IDs are
rejected. The shipped configuration continues to list all four seats so operators can select
any roster without mounting another config.

Delete `resolve_adapter_path()` and any configuration-path traversal behavior that existed
only to support configured adapter paths.

## Runtime changes

### Adapter runner

- Resolve the reviewer definition before reading reviewer-specific configuration.
- Obtain the adapter path from the registry under the trusted runtime root.
- Reject unsupported stages through the registry.
- Continue to emit a deterministic skipped artifact for a configured but disabled seat.

### Process environment

- `_build_adapter_env()` obtains the credential name and endpoint family from the registry.
- Only the selected reviewer's declared credential is copied.
- Cursor receives no OpenRouter fallback credential.
- Endpoint validation dispatches on `endpoint_kind`, not string sets duplicated in
  `adapter_process.py`.

### Effort

- Validation checks `supports_effort` from the registry.
- Cursor continues to reject a separate effort value because reasoning depth is encoded in
  its model selector.

## Workflow and documentation consistency

Keep workflow matrices explicit and readable. Do not generate the entire YAML workflow from
Python.

Add one narrow contract test that parses:

- the GitHub review and critique matrices;
- the GitLab review and critique job names;
- the shipped config reviewer keys;

and asserts that each equals the registry reviewer ID set. This replaces scattered hardcoded
seat-list assertions; it must be the only such parity test.

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
- every registry adapter path exists, is under the trusted adapter directory, and is
  executable in the image;
- each registry credential name is unique where required and matches workflow secret routing;
- review and critique are supported for every seat;
- Cursor is the only seat without effort support;
- config rejects `adapter` and `credential_variable` keys;
- config rejects unknown reviewer IDs;
- adapter runner uses the registry path, not a config path;
- credential isolation tests are parameterized over all four seats;
- GitHub and GitLab workflow seat sets match the registry;
- shipped config seat set matches the registry;
- no second reviewer ID list remains in Python source outside tests that explicitly compare
  external templates to the registry.

## Acceptance criteria

- Operator config contains no executable path or credential-variable wiring.
- Four first-party adapters remain independently selectable.
- One registry is the runtime authority for reviewer identity and immutable metadata.
- Provider-specific CLI behavior remains in provider adapters.
- Workflow and config seat parity is checked once.
- Cursor remains supported and its trust boundary is documented without a large runtime
  permission test.
- `make quality` passes.

## Non-goals

- Do not add third-party adapter discovery.
- Do not add entry points, dynamic imports, or project-supplied executable paths.
- Do not reduce the number of first-party adapters.
- Do not combine provider shell scripts into one branch-heavy script.
- Do not move provider CLI flags into the registry.
