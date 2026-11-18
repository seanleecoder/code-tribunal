# Testing strategy

Tests are organized by the boundary they protect:

- `unit/` pins parsing, configuration, adapters, consensus, posting, and
  workflow contracts.
- `contract/` keeps platform implementations and golden consensus cases aligned.
- `integration/` exercises prepare and publish behavior across fake platforms
  (`test_publish_e2e.py`), and the cross-revision thread lifecycle
  (`test_revision_lifecycle_e2e.py`).
- `security/` covers hostile model text and state authenticity.

Repository CI runs `make quality`. Live platform checks run in the Candidate
Canary before each release, because local fakes cannot prove protected-variable
behavior, platform token scope, required-check configuration, or real container
registry pulls. See the [release process](release-process.md).

When behavior changes, update the smallest relevant test and any schema-backed
golden file. Run `make update-golden` only for an intentional reducer contract
change and review the generated diff.

Test artifact producers and admission boundaries against the shipped JSON
schemas, with concrete artifacts; see [module ownership](architecture.md#module-ownership).

`make test` invokes pytest directly with coverage. `make docs-check` uses the
pinned native Lychee binary to check local Markdown links and anchors. The
historical `release/<version>.md` notes are frozen and not link-checked. GitLab
installation examples are validated by the pipeline-trust tests. Release tooling
is covered by `test_check_release.py`, which builds throwaway repositories and a
fixture signing key. IDs in the temporary-compatibility register are checked
against their code markers by `test_temporary_compatibility.py`.

## Codex image gate

`scripts/smoke_codex_batch_output.sh <reviewer-image>` runs the real pinned
Codex CLI, its shipped adapter and registry arguments inside the reviewer image
with `--network none` and default container security options. A scripted
loopback Responses provider drives code-mode tool calls; no credential or paid
provider is used. Only the provider URL is redirected. The image workflow runs it
unconditionally, including on CLI pin bumps, and image handoff depends on it.

Codex runs without a shell (see the
[ADR-0003 amendment](../decisions/0003-product-invariants-and-complexity-envelope.md#amendment-2026-11-17-no-shell-codex)).
The gate collects every case under an overall bound and reports setup and
transport failures as failures:

- Both configured models read a random snapshot-only sentinel through
  `mcp__repo__read_file`; empty reviews and tool-free critiques stay usable,
  and critiques are offered no repository tool.
- Listing and grep find the sentinel, and each call's own result is checked, so a
  failed listing or search cannot hide behind a later success; a no-match search
  returns a successful empty result. A tool-reported missing file and recovery
  after one stay usable. Two hundred full-size reads in one code-mode cell stay
  usable too: the adapter drops tool arguments and results the parser never
  reads, so they cannot reach the runner's output limit. So does a provider
  stream dropped before completion: Codex reports the reconnect as a top-level
  `error` event, retries once, and completes. That notice is the only top-level
  error the parser admits, so a reworded notice fails this case.
- Containment: traversal and absolute paths are refused, and protected bytes
  outside the snapshot never reach the model. `exec_command`/`write_stdin` are
  absent at runtime; `apply_patch` is rejected by the read-only policy and
  snapshot bytes are unchanged, checked independently by the trusted parent.
  The code-mode and direct tool sets are pinned, and `ALL_TOOLS` must list the
  repository tools, because Codex defers MCP tools in code mode.
- Negative controls: malformed and truncated streams, a terminal provider
  failure, a missing code-mode host, an unavailable tool server, and a
  credential in the tool server's environment all yield unusable outcomes. A
  model outside the pinned CLI's catalog fails with the error that names that
  cause, so a reworded fallback-metadata warning fails this case.

Local Termux runs exercise the codec and driver logic, not Linux image
acceptance. The image gate proves the tool path with a scripted model that
already knows the tool names. A
[paid real-model check](https://github.com/seanleecoder/code-tribunal/actions/runs/44086897649)
before release established that the configured default model discovers the
deferred tools: it called `grep` and `read_file`, read an unchanged file the diff
called into, and completed without command execution. Release canaries repeat
that evidence per platform.

Configuration tables, headings, ordinary prose, and backticked code/test names
are reviewed by people rather than a documentation-policy checker. Review
changes to documented defaults, schema versions, and job names against their
[authorities](../decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
