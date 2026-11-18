# Codex exec stream captures

Captured on 2026-11-16 with the real Codex 0.169.0 Termux aarch64 build,
using `scripts/smoke_codex_batch_output.py` against a scripted loopback Responses
provider. No paid provider was contacted. Thread IDs, timestamps, checkout paths
and random sentinel values are replaced; event shapes, status/exit codes and
error text are preserved. These are parser evidence, not a Linux image acceptance
claim. The unconditional image workflow runs the same probe with the npm-pinned
Linux/amd64 CLI and its shipped adapter.

`missing-host` copies only the native binary to reproduce the pruned package
layout; it returns CLI exit 0 and valid final JSON, and emits no tool item.
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
