# Codex exec stream captures

Captured on 2026-11-16 with the real Codex 0.169.0 Termux aarch64 build,
using `scripts/smoke_codex_batch_output.py` against a scripted loopback Responses
provider. No paid provider was contacted. Thread IDs, timestamps, checkout paths
and random sentinel values are replaced; event shapes, status/exit codes and
error text are preserved. These are parser evidence, not a Linux image acceptance
claim. The unconditional image workflow runs the same probe with the npm-pinned
Linux/amd64 CLI and its shipped adapter.

`missing-host` copies only the native binary, without the code-mode host beside
it; it returns CLI exit 0 and valid final JSON, and emits no tool item.
`terminal` returns a scripted Responses failure after an answer item.

The `mcp-*` captures come from the no-shell Linux/amd64 run in
[workflow run 44073952753](https://github.com/seanleecoder/code-tribunal/actions/runs/44073952753)
on 2026-11-17, under the default container launch policy. Thread IDs, random
sentinels, a temporary directory suffix and a stderr timestamp are replaced.
`mcp-read` reads through the repository MCP server. `mcp-tool-error` is a
tool-reported missing file: status `failed` with `error: null`, which stays
usable. `mcp-containment` holds two refused paths, then a successful read; its
stderr carries Codex's `apply_patch` rejection. `mcp-operational` is derived
from `mcp-read` by setting the item's `error`, the shape of a failed call.

`reconnect` and `separator` were captured on 2026-11-18 with the Termux aarch64
Codex 0.169.1 build against a loopback stub. Thread IDs are replaced and stderr,
which held only a Termux helper-alias warning, is cleared. In `reconnect` the
stub closes the first stream before `response.completed`. Codex reports
`Reconnecting... 1/5` as a top-level `error` event, retries, and completes with
exit 0. In `separator` the answer text ends in U+2028, which Codex writes
unescaped inside the event's JSON string.

`fallback-metadata` was captured on 2026-11-18 with the Termux aarch64 Codex
0.170.0 build against the gate's loopback stub, configured with a model that is
not in the CLI's catalog (renamed to `example/uncatalogued-model`; the thread ID
is replaced). Codex reports its fallback metadata as an `error` item before
`turn.started`, then completes with exit 0 and a valid answer.

`list-mcp-resources` and `host-session-failure` were captured on 2026-11-25
with the Termux aarch64 Codex 0.172.0 build against the gate's loopback stub;
the thread ID is replaced and the stderr timestamp normalized. In
`list-mcp-resources` the scripted model calls Codex's built-in
`list_mcp_resources`, which Codex reports as an `mcp_tool_call` from server
`codex`, then answers. In `host-session-failure` a host shim delegates to the
real host until the provider receives its first request and then exits 42.
Codex emits no tool or error item and exits 0 with a valid answer; only its
stderr router line records that the code-mode session failed to open.
