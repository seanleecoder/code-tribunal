# Claude stream-json captures

Captured on 2026-11-26 with Claude Code 2.1.325 in the consumer reviewer image
`ai-review-reviewer:3.1-a7066d4…@sha256:0bb096c3…`, through the image's own
`adapter_runner.run_adapter("claude", "review")` and registry argv. Only
`ANTHROPIC_BASE_URL` was redirected, to a scripted loopback Messages provider,
with `--network none` and a dummy key. No paid provider was contacted. Each
stream holds one scripted repository call, then the same `StructuredOutput`
answer. Streams are kept as captured, including session IDs, workspace paths and
random sentinels, none of which is secret.

- `grep-success` comes from
  [run 45805403141](https://github.com/seanleecoder/code-tribunal/actions/runs/45805403141):
  a content-mode `Grep` that returns the snapshot-only sentinel without using the
  image's `/usr/local/bin/rg`.
- The others come from
  [run 45807372796](https://github.com/seanleecoder/code-tribunal/actions/runs/45807372796),
  in a container without `DAC_OVERRIDE`/`DAC_READ_SEARCH`.
  - `read-success` returns the sentinel: no `is_error`, plus an object
    `tool_use_result`.
  - `read-eacces` reads the copied sentinel at mode 000: `is_error: true` with
    `EACCES: permission denied, open '<path>'`. The CLI still exits 0 with
    `structured_output`.
  - `read-missing` is a benign missing file: `is_error: true` with
    `File does not exist. …`.
  - `grep-no-match` is a benign empty search: no `is_error`, `No matches found`.

A benign missing file and a filesystem failure share `is_error: true`, so the
codec cannot classify by that flag alone. The Claude image gate reruns these
cases on every reviewer image.
