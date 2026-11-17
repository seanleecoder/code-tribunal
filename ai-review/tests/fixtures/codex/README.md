# Codex exec stream captures

Captured on 2026-11-16 with the real Codex 0.169.0 Termux aarch64 build,
using `scripts/smoke_codex_batch_output.py` against a scripted loopback Responses
provider. No paid provider was contacted. Thread IDs, timestamps, checkout paths
and random sentinel values are replaced; event shapes, status/exit codes and
error text are preserved. These are parser evidence, not a Linux image acceptance
claim. The unconditional image workflow runs the same probe with the npm-pinned
Linux/amd64 CLI and its shipped adapter.

`missing-host` copies only the native binary to reproduce the pruned package
layout. Both that case and `all-tools-failed` return CLI exit 0 and valid final
JSON. A failed launch emits command status `failed`, exit -1 and a router
error; a missing host emits no command item. `recovery` follows a failed launch
with a successful read. `no-match` runs grep with exit 1 and must stay usable.
`terminal` returns a scripted Responses failure after an answer item.

`linux-namespace-denied` is captured from the Linux/amd64 image gate in
[job 133651082433](https://github.com/seanleecoder/code-tribunal/actions/runs/43967617911/job/133651082433)
on 2026-11-16 (reviewed source head `71fb1ef5f705`; PR merge build
`7fcb414968d61be6abac00c874709ccffb27445f`). Only the thread ID is replaced.
The `/bin/bash -lc` command exits 1 with a `bwrap:` namespace-permission error,
while the CLI exits 0 and emits a valid final batch. This proves failure
admission for the observed Linux shape; it does not identify the denying layer
or demonstrate a working launch policy.
