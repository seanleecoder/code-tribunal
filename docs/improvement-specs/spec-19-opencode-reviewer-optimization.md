# SPEC-19 — OpenCode reviewer cost/latency optimization

- **Severity:** High (operational cost + timeout flakiness) · **Effort:** S · **ROI rank:** n/a (post-Phase-3)
- **Depends on:** none. SPEC-20 (reviewer usage accounting) is strongly recommended
  alongside: it is the instrument that proves this spec's effect and detects
  silently-ignored config keys.

## Decision changelog

- **2026-07-15 — turn-cap policy broadened.** The earlier OpenCode-only plan
  retained the existing opt-in `max_turns` / `AI_REVIEW_MAX_TURNS` contract.
  The implementation decision supersedes that plan: remove turn-cap controls
  for every adapter, including Claude's `--max-turns` path. `timeout_seconds`
  is the sole hang-catch. This is a deliberate breaking operator-contract
  change, not an incidental OpenCode optimization.

## Why

Real runs show the `opencode` reviewer is the slowest and most expensive seat on
the panel: it sometimes hits its 600s `timeout_seconds`, and on
`google/gemini-3.1-flash-lite` it costs more per review than the `claude`
(Haiku 4.5) and `codex` (gpt-5.4-mini) reviewers on stronger models.

All three reviewers receive the identical rendered prompt
(`ai-review/prompts/review.md` + manifest + diff) and the same read-only
repo-snapshot access, so the difference is in how the OpenCode CLI frames and
runs its agent loop. Root-cause chain, verified against the pinned
`opencode-ai` 1.17.18 and OpenCode docs/source (anomalyco/opencode, 2026-07):

1. **No prompt caching for non-Anthropic models via OpenRouter.** OpenCode's
   provider transform applies `cacheControl: {type: "ephemeral"}` breakpoints
   Anthropic-style only (`packages/opencode/src/provider/transform.ts`,
   `applyCaching`). Gemini-family models routed through OpenRouter get no cache
   breakpoints, so **every agent turn re-sends the entire accumulated context**
   (system scaffolding + rendered prompt with full diff + all prior tool
   output). Token cost therefore grows superlinearly with turn count.
2. **Unbounded agent loop.** The generated `ai-reviewer` agent config in
   `ai-review/adapters/opencode.sh` sets no `steps` value, and OpenCode's
   default is unlimited iterations (`packages/opencode/src/session/prompt.ts`:
   `const maxSteps = agent.steps ?? Infinity`). Combined with (1), an
   exploratory model multiplies the full-context cost per extra turn and can
   run until the runner's process-group kill. This is an accepted trade-off:
   the 2026-07-15 policy deliberately keeps voluntary completion and relies on
   `timeout_seconds` as the hang-catch instead of reintroducing a turn cap.
3. **Tool-schema overhead on every request.** The adapter denies most tools via
   the `permission` map, but a `permission: deny` entry only blocks *execution*
   — the tool's JSON schema/description is still sent with every request.
   Upstream measurement puts the full built-in tool description set at roughly
   3,000–4,000 tokens per request (anomalyco/opencode issue #11995). Removing a
   tool from the request schema requires the (deprecated-but-working) boolean
   `tools` map instead.
4. **No reasoning-effort control.** `reviewers.opencode.effort` /
   `AI_REVIEW_OPENCODE_EFFORT` is parsed, validated against the closed set
   `{low, medium, high, xhigh, max}`, and exported to every adapter as
   `AI_REVIEW_EFFORT` (`adapter_runner._build_adapter_env`), but only
   `claude.sh` consumes it (`--effort`). `opencode.sh` ignores it, so reasoning
   depth on the cheapest panel seat is uncontrollable — and reasoning tokens
   are re-billed on every uncached turn per (1).
5. **LSP/formatter startup.** OpenCode can spin up LSP servers and formatters;
   the reviewer never edits files, so these contribute startup latency and
   memory with no review-visible signal. Defaults have shifted across OpenCode
   versions, so the off-state must be pinned explicitly.

## Decisions

### Superseded decision record

- **No default turn cap (superseded 2026-07-15).** The original plan was to
  keep the existing opt-in `max_turns` / `AI_REVIEW_MAX_TURNS` controls, ship
  no default in `review.yaml`, and wire the OpenCode control to agent `steps`.
  This preserves the record of the earlier decision; it is no longer the
  implementation policy.

### Current decisions (do not relitigate in implementation)

- **No turn-cap controls.** The operator preference is no hard cap on agentic
  turns. Do not wire OpenCode `steps`, ship no `max_turns` value in
  `review.yaml`, and do not expose `AI_REVIEW_MAX_TURNS` /
  `AI_REVIEW_<REVIEWER>_MAX_TURNS` controls. Timeout remains the hang-catch.
- **No quality degradation by default.** Default-on changes are limited to
  no-signal-loss items: schema-trimming tools that are already
  permission-denied (this also stops the model wasting turns attempting calls
  that would be denied) and pinning LSP/formatters off (parity: the claude and
  codex reviewers have no LSP either). `effort` ships **unset** (provider
  default); `low` is a documented recommendation for flash-class models, to be
  adopted once SPEC-20 makes the trade-off measurable.
- **Prompt parity.** `ai-review/prompts/review.md` stays byte-identical across
  reviewers. Replacing OpenCode's built-in system prompt (agent `prompt` key)
  is explicitly out of scope: each CLI keeps its native scaffolding.

## Scope

- **In:** `ai-review/adapters/opencode.sh` (the generated
  `OPENCODE_CONFIG_JSON` heredoc plus a small preamble block);
  `ai-review/adapters/claude.sh` (remove `--max-turns`); the turn-cap paths in
  `ai-review/src/ai_review/adapter_runner.py` and
  `ai-review/src/ai_review/config.py`; `ai-review/config/review.yaml`
  **comments only**; and unit tests in `test_openrouter_adapters.py`,
  `test_adapter_runner.py`, and `test_config_env_overrides.py`.
- **Out:** `ai-review/prompts/*` (parity); any default `effort` value in
  `review.yaml`; OpenCode agent `prompt` replacement; the codex adapter.

## Implementation

OpenCode's generated config JSON is built in an **unquoted heredoc**
(`OPENCODE_CONFIG_JSON=$(cat <<EOF ... )`), so every interpolated value must
be injection-safe (see each item).

1. **Remove all turn-cap controls.** Remove `max_turns` from the reviewer
   schema, stop exporting `AI_REVIEW_MAX_TURNS` through the runner allowlist,
   and remove Claude's `--max-turns` argument. Delete the old pass-through
   tests and replace them with a config-validation test proving
   `reviewers.<name>.max_turns` is rejected. Existing `error_max_turns` parsing
   remains because it is a Claude CLI result subtype, not a supported control.

2. **Wire supported `AI_REVIEW_EFFORT` values → agent `reasoningEffort`.**
   Before the heredoc:

   ```sh
   # OpenRouter accepts only low|medium|high. Higher Claude-specific values
   # deliberately leave the provider default in place; do not clamp them.
   case "${AI_REVIEW_EFFORT:-}" in
     low|medium|high) OPENCODE_REASONING_EFFORT="$AI_REVIEW_EFFORT" ;;
     *)               OPENCODE_REASONING_EFFORT="" ;;
   esac
   ```

   When non-empty, emit inside the `agent.ai-reviewer` block:
   `"reasoningEffort": "$OPENCODE_REASONING_EFFORT",`. OpenCode passes unknown
   agent keys through to the provider as model options (documented behavior:
   https://opencode.ai/docs/agents/). Injection safety: the value comes from a
   closed set validated in `config.py` (`EFFORT_LEVELS`), and the shell `case`
   both re-guards it and omits Claude-only `xhigh`/`max` instead of coercing
   them.

   Build the optional JSON fragment into a variable (e.g.
   `OPENCODE_AGENT_EXTRA_JSON`) and interpolate that into the heredoc, so the
   emitted config contains no dangling keys when the option is absent.

3. **Trim denied tools from the request schema.** Add to the
   `agent.ai-reviewer` block (and keep the existing `permission` maps exactly
   as they are — permission remains the enforcement layer; `tools` is the
   token-saving layer):

   ```json
   "tools": {
     "bash": false,
     "edit": false,
     "write": false,
     "patch": false,
     "webfetch": false,
     "websearch": false,
     "task": false,
     "todowrite": false,
     "todoread": false,
     "skill": false
   }
   ```

   Note: the `tools` map is deprecated upstream in favor of `permission`, but
   it is the only mechanism that removes a tool's schema from the request
   payload (`permission: deny` still ships the schema and lets the model burn a
   turn attempting a denied call). Record this rationale as a comment in the
   heredoc.

4. **Pin LSP and formatters off.** At the config top level (sibling of
   `"provider"`): `"lsp": false, "formatter": false`. Keep the existing
   `OPENCODE_DISABLE_LSP_DOWNLOAD=1` env var (belt and braces; it only prevents
   downloads, not startup).

5. **`config/review.yaml` comment updates only** (no value changes):
   - On `reviewers.opencode`: document that `effort` /
     `AI_REVIEW_OPENCODE_EFFORT` now reaches opencode as `reasoningEffort`
     (previously claude-only), that it accepts only `low`/`medium`/`high`, and
     that `low` is the recommended starting point for flash-class models once
     usage numbers (SPEC-20) are available.

## Acceptance criteria

- Unit suite green (`python -m unittest discover` from `ai-review/`).
- Generated opencode config (observable via the fake-CLI harness) contains, by
  default: the `tools` map above, `"lsp": false`, `"formatter": false`, and
  **no** `"reasoningEffort"` key.
- With `AI_REVIEW_OPENCODE_EFFORT=low`: config contains
  `"reasoningEffort": "low"`; with `xhigh` or `max`: the key is absent and
  OpenCode uses its provider default.
- A `max_turns` key in `review.yaml` fails config validation.
- One real review run (`AI_REVIEW_REQUIRE_REAL_OPENCODE=1`, real
  `OPENROUTER_API_KEY`, fixture MR) completes within `timeout_seconds` and
  produces a valid finding batch.
- With SPEC-20 landed: before/after comparison of `usage` in
  `out/status/opencode.json` shows reduced input tokens per run on the same
  fixture MR.

## Tests

Extend `ai-review/tests/unit/test_openrouter_adapters.py` (the fake-CLI harness
already writes a fake `opencode` executable that dumps argv/env — the generated
config is recoverable from the `OPENCODE_CONFIG_CONTENT` env var it records):

- Extend `test_opencode_real_path_invokes_opencode_cli`: parse the recorded
  `OPENCODE_CONFIG_CONTENT` as JSON and assert the default shape (tools map
  entries false, `lsp` false, `formatter` false, `reasoningEffort` absent,
  existing `permission` map unchanged).
- New: `AI_REVIEW_OPENCODE_EFFORT=low` (via `extra_env`) → `reasoningEffort ==
  "low"`; `AI_REVIEW_OPENCODE_EFFORT=xhigh` or `max` → `reasoningEffort`
  absent.
- New: a `max_turns` reviewer key fails config validation, preventing accidental
  restoration of the removed turn-cap contract.
- Assert the emitted config is valid JSON in every case (guards the optional
  fragment assembly against trailing-comma mistakes).

## Risk / rollback

- **Silently ignored keys:** if the pinned opencode-ai 1.17.18 ignores any of
  `tools`/`lsp`/`formatter`/`reasoningEffort`, the change degrades to a no-op,
  not a failure. SPEC-20's per-run token fields are the detector: unchanged
  token counts mean a key is not being honored.
- **Behavioral risk:** near zero for the default set — trimmed tools are
  already denied, and LSP/formatters produce no reviewer-visible signal
  (the reviewer never edits, so diagnostics never fire).
- **Rollback:** revert `adapters/opencode.sh`. No schema, state, or artifact
  contract is touched by the OpenCode optimization. The separate, intentional
  turn-cap policy change rolls back with `claude.sh`, `adapter_runner.py`,
  `config.py`, and its validation test as one unit.
