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
provider is used. The loopback runner forwards the allowed certificate-path
variables and requires the two default CA bundle paths in the reviewer image.
Only the provider URL is redirected. The image workflow runs the gate
unconditionally, including on CLI pin bumps, and image handoff depends on it.

Codex runs without a shell (see the
[ADR-0003 amendment](../decisions/0003-product-invariants-and-complexity-envelope.md#amendment-2026-11-17-no-shell-codex)).
The gate collects every case under an overall bound and reports setup and
transport failures as failures:

- Both configured models read a random snapshot-only sentinel through
  `mcp__repo__read_file`; an empty review and a tool-free critique stay usable,
  and critiques are offered no repository tool.
- Listing and grep find the sentinel, and each call's own result is checked, so a
  failed listing or search cannot hide behind a later success; a no-match search
  returns a successful empty result. A tool-reported missing file and recovery
  after one stay usable, and so does a provider stream dropped before completion: Codex reports the reconnect as a top-level
  `error` event, retries once, and completes. That notice is the only top-level
  error the parser admits, so a reworded notice fails this case.
- Containment, for each of the two models: traversal and absolute paths are
  refused, and protected bytes outside the snapshot never reach the model. `exec_command`/`write_stdin` are
  absent at runtime; `apply_patch` is rejected by the read-only policy and
  snapshot bytes are unchanged, checked independently by the trusted parent.
  The code-mode and direct tool sets are pinned, and `ALL_TOOLS` must list the
  repository tools, because Codex defers MCP tools in code mode.
- Verified filesystem faults run in a second container that drops only
  `DAC_OVERRIDE` and `DAC_READ_SEARCH`. The copied sentinel is mode 000, and each
  case first proves that precondition from the runner process. A failed read or
  search must reach the stream as a failed call (an item `error`, not a
  tool-reported result). A run whose attempts all failed this way is unusable
  even though the stub answers. A later successful call recovers the run. An
  invalid search pattern stays a tool-reported result.
- Negative controls: a terminal provider failure, a missing code-mode host
  (named from Codex's startup item), an unavailable tool server, and a
  credential in the tool server's environment all yield unusable outcomes. The
  last two must also show their own cause in Codex's stderr (a missing command,
  or a server that started and refused), so a rejected override cannot pass
  them. Malformed and truncated streams, a host that fails to open a session,
  and searches that all time out are covered by the codec's and tool server's
  unit tests.

## Claude image gate

`scripts/smoke_claude_batch_output.sh <reviewer-image>` runs the real pinned
Claude CLI through the image's own runner, registry argv and admission with
`--network none`. Only `ANTHROPIC_BASE_URL` is redirected, to a scripted
loopback Messages provider; no credential or paid provider is used. The stub
sends each repository call only after the previous call's result comes back.
The image workflow runs the gate unconditionally before image handoff.

- The stock container covers `Read`, `Grep` and `Glob`. Each must return the
  snapshot-only sentinel or file. A critique must be offered no repository tool.
- A second container drops only `DAC_OVERRIDE` and `DAC_READ_SEARCH`.
  - A matched `Read` control must still succeed.
  - The fault case reads the copied sentinel at mode 000. The CLI must report
    `EACCES: permission denied, open '<path>'` and still supply its answer.
    The runner must record `model_error`. Each fault case first proves its
    precondition from the runner process.
  - A missing file, a no-match search, and a fault followed by a successful read
    stay `success`.

The [captured streams](../../ai-review/tests/fixtures/claude/README.md) behind
the codec's benign allowlist came from the same harness.

Local Termux runs exercise the codec and driver logic, not Linux image
acceptance. The image gate proves the tool path with a scripted model that
already knows the tool names. A
[paid real-model check](https://github.com/seanleecoder/code-tribunal/actions/runs/44086897649)
before release established that the configured default model discovers the
deferred tools: it called `grep` and `read_file`, read an unchanged file the diff
called into, and completed without command execution.

The Candidate Canary panel adds a gateway caller whose behavior depends on the
unchanged demo access helper on GitHub and GitLab. The generated panel jobs pin
the candidate reviewer image and invoke the shipped launcher. The trusted runner
asks Codex to inspect the helper and records the result from its execution stream
in memory after normal review admission. A completed `read_file` or `grep` result
must contain content from the helper. The panel validator checks the result
against the run, effective configuration, platform, fixture, and reviewer image.
The canary artifact contains only the bound verdict. The panel summary identifies
the tested model and reports `repository_read: true` when the proof succeeds.
Build the candidate images from a source revision that includes the runner's
canary capture. Ordinary reviews may complete with no findings or tool calls.
The separate lifecycle campaign exercises live inline anchoring on a modified
existing file.

Configuration tables, headings, ordinary prose, and backticked code/test names
are reviewed by people rather than a documentation-policy checker. Review
changes to documented defaults, schema versions, and job names against their
[authorities](../decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
