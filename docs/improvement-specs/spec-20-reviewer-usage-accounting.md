# SPEC-20 — Per-reviewer token/cost usage accounting

- **Severity:** Medium (observability gap blocking cost decisions) · **Effort:** S · **ROI rank:** n/a (post-Phase-3)
- **Depends on:** none. Complements SPEC-19 (opencode optimization): this spec
  is the measurement instrument that validates SPEC-19's effect and detects
  config keys the opencode CLI silently ignores. SPEC-21 (cursor reviewer)
  interacts trivially (cursor's usage is `null`).

## Why

Operators report that the opencode reviewer "costs more on a cheaper model than
the other reviewers on stronger models" — but the pipeline records **no token
or cost data at all**, so such reports cannot be confirmed, attributed, or
tracked across changes. The data is already present in adapter stdout and is
currently discarded:

- **opencode** (`--format json`): the NDJSON stream contains `step_finish`
  events whose `part` payload is
  `{"type": "step-finish", "reason": ..., "cost": <number>, "tokens":
  {"input": n, "output": n, "reasoning": n, "cache": {"read": n, "write": n}}}`
  — one per agent step (verified against opencode SDK
  `types.gen.ts` / `cli/cmd/run.ts` for the pinned 1.17.x line).
- **claude** (`--output-format stream-json --verbose`): the terminal
  `{"type": "result", ...}` event carries a `usage` object
  (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`), `total_cost_usd`, and `num_turns`.
  **Verify exact field names against the pinned CLI 2.1.207 by capturing a
  real stream before writing the fixtures** — the shape above is the expected
  one, and fixture-driven tests make a wrong guess cheap to fix.
- **codex**: token counts appear only as stderr narration; stdout is the
  `--output-schema` result file. Out of scope for v1 (see Scope).
- **cursor** (SPEC-21): the CLI emits no usage fields at all (open upstream
  feature request as of 2026-07).

`adapter_runner._load_stream_json` walks every one of these events today and
keeps only the findings JSON. The status artifact
(`out/status/<reviewer>.json`, `adapter_status.schema.json`) already records
timing (`duration_ms`) — usage is the missing sibling.

This spec deliberately stops at **accounting**. Spend *enforcement* (budgets,
per-run caps) remains archived in
`docs/archived-improvement-plans/spend-enforcement.md` and must not be
reintroduced here.

## Scope

- **In:** `ai-review/src/ai_review/adapter_runner.py`,
  `ai-review/src/ai_review/schema.py` (`adapter_status_artifact`),
  `ai-review/schemas/adapter_status.schema.json`,
  `ai-review/tests/unit/test_adapter_runner.py` (+
  `test_schema_validation.py` if it validates status artifacts).
- **Out:** spend enforcement/budgets; codex stderr parsing (note as follow-up
  in the code comment); consensus/gate/post stages (nothing reads status
  artifacts back — verified: the runner is the only writer and no `ai_review`
  module consumes them); dashboards/aggregation.

## Implementation

1. **New pure function in `adapter_runner.py`:**

   ```python
   def _extract_usage(stdout: str) -> dict[str, Any] | None:
   ```

   Deliberately **separate from `_load_stream_json`** — that function returns
   only the findings root and raises on malformed streams, while usage must
   also be recoverable on error and timeout paths. Behavior:

   - Scan `stdout` line by line; skip lines that are empty or not valid JSON
     objects (tolerant scan — never raise).
   - **opencode shape:** for events where `event["part"]` is a dict with
     `part["type"] == "step-finish"`, sum `part["tokens"]["input"|"output"|
     "reasoning"]`, `part["tokens"]["cache"]["read"|"write"]`, and
     `part["cost"]`; count matching events as `steps`. Guard every access
     (missing keys → treat as 0). Comment the known upstream caveat: OpenRouter
     cache-write tokens are under-reported (anomalyco/opencode #18440) — record
     what the CLI reports, do not work around it.
   - **claude shape:** for the terminal `event["type"] == "result"` (also
     accept the single-object `--output-format json` envelope, i.e. when the
     whole stdout is one result object), read `usage.input_tokens` →
     `input_tokens`, `usage.output_tokens` → `output_tokens`,
     `usage.cache_creation_input_tokens` → `cache_write_tokens`,
     `usage.cache_read_input_tokens` → `cache_read_tokens`,
     `total_cost_usd` → `total_cost_usd`, `num_turns` → `steps`.
   - Return `None` when no usage-bearing event was seen (codex, cursor, mock).
   - Normalized return shape (all values `int | float | None`):

     ```python
     {
       "input_tokens": ..., "output_tokens": ..., "reasoning_tokens": ...,
       "cache_read_tokens": ..., "cache_write_tokens": ...,
       "total_cost_usd": ..., "steps": ...,
       "source": "opencode_step_finish" | "claude_result",
     }
     ```

2. **Thread through the status artifact.**
   - `schema.adapter_status_artifact(...)` gains a keyword-only
     `usage: dict[str, Any] | None = None` parameter and always includes
     `"usage": usage` in the artifact dict.
   - `adapter_runner._write_status(...)` gains the same pass-through kwarg.
   - In `run_adapter`: call `usage = _extract_usage(result.stdout)` once after
     `_run_adapter_process` returns, and pass it to `_write_status` on the
     **success**, **parse-error** (`model_error`/`schema_error`), and
     **AdapterExit** paths. On the **timeout** path, call
     `_extract_usage(exc.output or "")` — partial usage from a killed opencode
     run (how many steps and tokens it burned before the kill) is precisely the
     diagnostic the cost/timeout complaint needs. `skipped` /
     `config_error` / `internal_error` paths keep the default `None`.

3. **Schema:** add an optional `usage` property to
   `ai-review/schemas/adapter_status.schema.json`:
   `{"type": ["object", "null"], "additionalProperties": false, "properties":
   {each normalized field, "type": ["number", "null"] (or ["integer","null"]
   for token/step counts), "source": {"type": ["string", "null"]}}}`.
   Do **not** add `usage` to `required` — previously written artifacts must
   keep validating. (New artifacts always carry the key, possibly `null`,
   because `adapter_status_artifact` always emits it.)

4. **Job-log visibility:** when `usage` is not `None`, write one redacted
   stderr line so cost is visible without downloading artifacts:

   ```
   ai-review: <reviewer> <stage> usage: in=<n> out=<n> reasoning=<n> cache_r/w=<n>/<n> cost=$<x> steps=<n>
   ```

   (Use `redact_text` like the existing `_log_structured_output_usage` line.)

## Acceptance criteria

- Unit suite green.
- After a panel run (mock or real), every written status artifact contains a
  `usage` key; opencode's (real run) carries summed tokens/cost/steps;
  codex/cursor/mock carry `null`.
- A timed-out opencode run's status artifact carries the partial usage
  accumulated before the kill.
- All written artifacts validate against the updated
  `adapter_status.schema.json`; a pre-change artifact (no `usage` key) also
  still validates.
- The job log shows the `ai-review: ... usage: ...` line for usage-bearing
  reviewers.

## Tests

In `ai-review/tests/unit/test_adapter_runner.py`:

- New `UsageExtractionTests` (pure-function tests for `_extract_usage`):
  - opencode fixture with two `step_finish` events → summed tokens/cost,
    `steps == 2`, `source == "opencode_step_finish"`.
  - claude stream fixture (assistant events + terminal result with `usage`,
    `total_cost_usd`, `num_turns`) → mapped fields,
    `source == "claude_result"`; single-object claude JSON envelope variant.
  - Stream with interleaved non-JSON garbage lines → still extracts.
  - Stream with no usage events (codex-style plain JSON) → `None`.
  - Missing sub-fields (e.g. no `cache` object) → zeros/None, no exception.
- Extend the existing status end-to-end tests (fake-adapter pattern used by
  e.g. `test_timeout_archives_partial_output_for_debugging`):
  - success path: status artifact contains populated `usage`, validates
    against the schema;
  - timeout path: fake adapter emits `step_finish` events then sleeps →
    status carries partial usage;
  - parse-error path: usage still recorded alongside `schema_error`.
- In `test_schema_validation.py` (or nearest schema test): a legacy status
  artifact **without** `usage` validates; one **with** `usage: null` and one
  with a populated object validate; an object with an unknown sub-key fails
  (`additionalProperties: false`).

## Risk / rollback

- Additive optional field; no consumer reads status artifacts today, so there
  is no downstream contract to break.
- Claude field names are the only externally-shaped guess — fixture-driven
  tests plus one real captured stream close that quickly.
- Cost figures for OpenRouter runs are indicative, not billing-grade
  (upstream cache-write under-reporting, opencode #18440) — documented in the
  code comment and this spec.
- Rollback: revert the three files; artifacts written meanwhile remain valid
  under the reverted schema (the `usage` property was optional).
