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

## Temporary Codex sandbox investigation

COMPAT-012 adds a four-way diagnostic after a failed Codex image gate, on the
same Ubuntu runner and resolved Linux/amd64 image ID. To run it on a Linux Docker
host with an already-built reviewer image:

```bash
python3 scripts/diagnose_codex_sandbox.py <reviewer-image> /tmp/codex-sandbox-evidence
```

The evidence directory must be new. The default, seccomp-only, AppArmor-only and
combined profile experiments use no provider secrets and no external network.
Each container has a 180-second bound and explicit removal on timeout; the CI
step has a 16-minute bound. Setup/transport failures remain distinct from a
reproduced namespace denial. A missing or successful baseline, four denied reads,
or incomplete evidence makes the diagnostic fail. The original image gate stays
failed regardless of diagnostic results.

Only the read case using the real Sol model ID runs in this diagnostic subset.
The ordinary gate collects the previous 13 cases plus a read-only enforcement
case under its existing 900-second wrapper bound. The latter requires a random
snapshot sentinel read, denied mutations/deletions and protected runtime/input
writes, and unchanged bytes observed by the trusted parent after CLI exit and
before snapshot cleanup. A namespace failure cannot satisfy that check.

Runtime evidence records kernel/runner/Docker identity, process seccomp and
capability status, AppArmor context, user-namespace sysctls, available audit data,
and system/bundled bwrap paths, hashes and help/version output. Unavailable
observations are recorded explicitly. The workflow uploads the detailed evidence
as `codex-sandbox-diagnostic`, retained for seven days. Console output contains
case classifications and the summary. Termux unit tests validate collection and
failure admission, not Linux enforcement or deployment compatibility.

A successful relaxation identifies a candidate restriction to investigate with
audit evidence or a targeted probe. It does not select a production policy.
Remove COMPAT-012 after collecting evidence. Production deployment changes,
GitHub/GitLab compatibility claims and release validation require the subsequent
policy decision and live checks described in the agreed PR #304 plan.

The initial bounded Linux experiment did not demonstrate a working launch
arrangement.
The ordinary gate ran all 14 cases and stayed failed. Individual outer-profile
relaxations and their combination did not produce a successful sentinel read.
Some sandbox failures reached only the provider tool response, with no failure
signal in the CLI stream or stderr. The codec cannot distinguish those streams
from tool-free success; the sentinel gate correctly rejects them. A successful
adapter outcome alone is therefore insufficient evidence of usable tools.
Its follow-up is the explicit managed-profile experiment below. Detailed
evidence remains in the separately
approved diagnostic artifact; it is not copied into repository documentation.

The follow-up stays within those same four launch variants and existing
privileges. Each diagnostic container also runs fresh child processes for
user-only, user+mount, user+network and combined namespace creation. They record
the syscall/errno for namespace creation, self UID/GID mapping, mount propagation
and an IPv4 loopback address request. Namespace identity must change before any
mapping, mount or interface operation; each child has a five-second bound.
These are isolated operation probes, not a replacement for bwrap or a policy
enforcement test. A failed syscall is an observation; a failed child/transport
is explicitly incomplete diagnostic setup. Unexplained denials stop the scope
without further privilege relaxation.

The generated-repository diagnostic read also enables the pinned CLI's internal
`CODEX_ROLLOUT_TRACE_ROOT` instrumentation. The source defines it in
[`ThreadTraceContext::start_root`](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/rollout-trace/src/thread.rs)
and records nested results in
[tool dispatch tracing](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/core/src/tools/tool_dispatch_trace.rs).
Raw traces stay in the container's private `/tmp` and are deleted before exit;
they are never copied into the uploaded evidence mount. Only fixed status counts,
completeness flags and the presence of a sandbox diagnostic are exported. The
experiment tests whether the private trace retains a failure hidden from CLI
events; it does not add a production admission contract or change environment
scrubbing. Missing or malformed observations make diagnostic setup incomplete.

The next candidate uses the pinned upstream Moby profiles under
[`scripts/codex_profiles/`](../../scripts/codex_profiles/). Both source files
retain their original bytes and are checked against SHA-256 values. The
seccomp candidate appends allowances for bounded namespace flags, mount,
pivot-root and detach-unmount operations; all unrelated baseline rules remain.
The AppArmor candidate preserves the upstream deny rules except mount denial,
and adds explicit namespace, mount and pivot-root permissions under ABI 4.0,
with Unix networking stated explicitly. The renderer and generated manifest
make that delta reviewable. The pinned baseline is not claimed to match a
particular Docker daemon's compiled-in profile byte for byte.

This exposes additional kernel interfaces to processes in the experimental
container. It is a feasibility candidate, not a final minimal or accepted
policy for shared runners. The initial workflow step only compiles the profile:
[`apparmor_parser -Q -K`](https://manpages.ubuntu.com/manpages/resolute/man8/apparmor_parser.8.html)
does not load kernel policy or use privileged caches. Local Termux checks lack
the parser and Docker; report Linux compilation separately.

The temporary `codex_managed_policy_experiment` workflow-dispatch input defaults
to false. It runs only on a non-main branch and an ephemeral GitHub-hosted Linux
VM, after compilation succeeds. The controller checks the daemon is local and
regenerates all policy files before loading a uniquely named AppArmor profile
with add-only semantics. It tests the pinned baseline, each candidate profile
separately, and both together on one resolved image. A combined successful read
is followed by all 14 smoke cases with exactly the same launch options. Each
read has a 180-second bound; the full suite has a 900-second bound, inside a
35-minute workflow step. No capabilities or host sysctl changes are added.

Containers carry a unique ownership label and are removed before unloading the
new profile. Unconfirmed container cleanup leaves the profile loaded until VM
disposal, because unloading could unconfine a live container. Existing profiles
are never replaced. Profile removal and unchanged observed host sysctls are
required for success. Evidence uses the same approved seven-day artifact, with
fixed classifications in the console. This explicit kernel-policy experiment
does not change production launches or waive the original failed image gate.
Delete its input and controller with COMPAT-012 before a supported policy ships.

The diagnostic also tests the public `codex sandbox` command as a potential
fail-fast check. Its read-only setting comes from the trusted registry. It uses
generated files and no provider/platform credentials, verifies a random read,
exit-code forwarding and an independently checked denied write. CLI state stays
outside uploaded evidence and is deleted. Only fixed results are archived.
The pinned [sandbox command implementation](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/cli/src/debug_sandbox.rs)
forwards child exit status, but real tool-context parity and production admission
remain unproven. No production adapter or deployment policy changes here.

The [manual experiment on `a07af01`](https://github.com/seanleecoder/code-tribunal/actions/runs/44033770758)
passed with both candidate profiles together. The pinned baseline and each
candidate used alone failed the read contract. The combined launch passed all
14 smoke cases, including both configured models, recovery/no-match controls,
and independent read-only enforcement. The public sandbox probe passed its
random read, exit-code forwarding and denied-write checks. Cleanup confirmed
container removal, profile absence and unchanged observed host sysctls. The
downloaded archive's digest was verified and it contained no raw trace files.

This establishes feasibility for that controlled hosted-VM run. The default
gate remains failed, and no images were handed off or published. A supported
runner policy still needs a decision about the added namespace/mount kernel
surface and provisioning before GitHub job-container startup or through GitLab
runner administration. Both platforms require their own live acceptance checks.

The admission candidate is a bounded, credential-free public sandbox check
before a model call, using the selected read-only configuration and execution
context. A failed check should produce an unusable execution outcome. The
experiment shows detection of the broken default environment and success with
the working candidate; it does not prove parity for every real tool invocation.
Keep the existing CLI-event admission and image gate, and review context parity,
timeout accounting and environment isolation before implementing this check in
the production runner. Private debug traces remain diagnostic-only.

The archived kernel audit records explain why disabling both outer profiles
still failed while the named candidates passed. The hosted Ubuntu kernel
restricts unprivileged user namespaces through AppArmor. A namespace created by
an unconfined process moves to a restricted profile that denied a capability
bwrap needs. Docker's default seccomp profile separately blocks namespace
creation, and its default AppArmor profile denies mounts and grants no user
namespace rule. All three layers block Codex's nested sandbox under default
launches.

Decision (2026-11-17): pursue a no-shell Codex reviewer rather than relaxing
runner kernel policy. Claude, OpenCode and Cursor already use read-only/no-shell
tool policies without an OS sandbox. Codex 0.169.0 can disable its shell tool
with the stable `features.shell_tool` flag. Its only repository-read path would
then be a trusted, read-only stdio MCP server configured with
`default_tools_approval_mode = "approve"`. Pinned source starts local stdio MCP
servers as ordinary child processes, outside bwrap, and code mode exposes MCP
tools. The next step is a bounded Linux experiment under default Docker
policies; source reading does not establish feasibility. The managed-policy
candidates remain evidence only, not a deployment direction.

`scripts/experiment_codex_no_shell.py` runs that experiment after a failed image
gate, on the same runner and image, with no security options, capabilities or
host changes. The smoke driver's `--no-shell-mcp` subset disables the shell tool
and gives review stages the experimental
[`codex_repo_tools_mcp.py`](../../scripts/codex_repo_tools_mcp.py) server, rooted
at the copied snapshot. It is standard-library Python with `read_file`,
`list_dir` and `grep`; it resolves every path, symlinks included, inside that
root, bounds output and has no write operation. Critique has no tools. Both
configured models must read a random snapshot-only sentinel through code mode.
A search case lists and greps for it, and a no-match search must stay usable.
The containment case requires refusals for traversal and absolute paths, no
offered or callable shell, unchanged snapshot/protected bytes, and no runner
credential in the server's environment names. The current codec fails closed
on `mcp_tool_call` items, so tool-using cases require exactly that
`schema_error`, an unchanged scripted final answer and no operational MCP error.
Admitting MCP items, including tool-reported errors such as a missing file, is
separate production work. Fixed item types and statuses go to the console and summary;
captures stay in the approved seven-day artifact. A pass would establish
feasibility only. A production tool server, codec admission for MCP failures,
and registry changes need their own reviewed change.

The [first run on `d3eeaab`](https://github.com/seanleecoder/code-tribunal/actions/runs/44068185860)
used the default launch policy. Both models read the random sentinel through
`mcp__repo__read_file` in code mode, and listing and grep returned it. Traversal
and absolute paths were refused. `exec_command` and `write_stdin` were
`undefined` at runtime. The offered `apply_patch` tool was rejected by Codex's
read-only policy, and snapshot bytes were unchanged. The server inherited only
`HOME`, `LC_CTYPE` and `PATH`. Every case still failed a textual check that found
a shell tool name in the request; it occurs only in Codex's fixed code-mode
prose, so that check now records context and relies on the runtime probe. Each
tool-using case produced the codec rejection above. The
[second run](https://github.com/seanleecoder/code-tribunal/actions/runs/44070156097)
passed both critiques but found no MCP tool description in the request's tool
lists. A fourth run showed why: in code mode Codex defers MCP tools. Its exec
description tells the model to find deferred tools in the `ALL_TOOLS` global,
which listed all three repository tools with typed declarations. Review cases
now require that guidance and the runtime listing; critique must still offer no
repository tool.

The [run on `e7e3dab`](https://github.com/seanleecoder/code-tribunal/actions/runs/44073952753)
passed all 10 no-shell cases under the default launch policy: both models'
sentinel reads, empty reviews and critiques, search, no-match, missing-file and
containment. The original image gate stayed failed and nothing was handed off
or published. This establishes feasibility on a hosted runner only. Production
still needs: codec admission of `mcp_tool_call` items that keeps tool-reported
errors usable and rejects operational failures; a reviewed tool server in the
`ai_review` package; registry arguments; a decision on disabling `view_image`
and `apply_patch`, which remain offered (the latter was rejected by the
read-only policy); the default gate switched to the no-shell cases; COMPAT-012
removal; and live GitHub/GitLab acceptance.

Configuration tables, headings, ordinary prose, and backticked code/test names
are reviewed by people rather than a documentation-policy checker. Review
changes to documented defaults, schema versions, and job names against their
[authorities](../decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
