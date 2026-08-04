# SPEC-50 — Enforce structured OpenCode reviewer output

- **Status:** Implemented (post-1.0; runtime source only; immutable image publication and live-provider acceptance remain separate rollout work).
- **Classification:** S / reviewer-transport correctness.
- **Severity:** High (the reviewer produced usable findings and the panel recorded zero, so the merge gate saw a silently degraded panel rather than a failure).
- **Effort:** M.
- **Supersedes:** [SPEC-49](spec-49-opencode-session-title-inference.md). SPEC-49's
  recorded decision — a fixed, data-free session title — remains in force; only its
  `opencode --pure run --title` *mechanism* is replaced, because the session is now
  created through the server API. SPEC-49's rationale is preserved unedited as the
  forensic record of the title-inference incident.
- **Depends on:** the existing OpenCode adapter contract in
  [SPEC-19](../history/specs/spec-19-opencode-reviewer-optimization.md).

## Incident and forensic rationale

GitLab job 2624957 (`AI review: [opencode]`, MR 3179, model
`deepseek/deepseek-v4-flash-0731`) failed after 237 seconds — not a timeout — with:

~~~text
status:        schema_error
error_class:   SchemaValidationError
error_message: "adapter output findings must be an array"
raw_finding_count: 0
~~~

The captured stream carried event types `step_start`, `tool_use`, `step_finish`. The
reviewer's finding batch existed only inside
`metadata.openrouter.reasoning_details[].text`, fenced; no assistant text part ever
carried it. The adapter runner treated reasoning text as answer text, extracted a JSON
root out of that prose, and rejected the result. The model had produced a usable
review; the transport lost it, and the error blamed the adapter for a model outcome.

Root cause: `opencode run --format json` selects raw event output. It does not
constrain the response shape. OpenCode was the only reviewer relying on the model
volunteering a schema-conforming batch, with prose recovery as the de-facto contract.

## Non-negotiable decision

The OpenCode adapter must obtain reviewer output through OpenCode's structured-output
transport, not by parsing model prose:

- The adapter starts `opencode --pure serve` on a loopback port and drives the session
  API through `ai_review.opencode_client`.
- Each stage's message request carries `format: {"type":"json_schema","schema": …}`
  with the stage schema (`raw_finding_batch.schema.json` for review,
  `critique_batch.schema.json` for critique), so OpenCode's StructuredOutput tool is
  required rather than optional.
- The client is the sole normalizer: it emits the reviewer batch itself, so the shared
  adapter runner reads it on its ordinary `findings`/`critiques` path. It must not
  emit another adapter's result envelope — doing so forces the shared runner to
  special-case OpenCode, which is what accumulated the branches this specification
  removes.
- Session creation keeps SPEC-49's fixed title `code-tribunal-ai-review`, unchanged in
  value and still free of prompt, repository, merge-request, and user data.

Structured output is the transport. Text is a compatibility path only, and it is
admitted under **one rule shared by every adapter**, implemented once in
`ai_review.adapter_output.extract_json_text` and used both by the adapter runner and by
the OpenCode client's fallback. A per-adapter variant of this rule is not permitted:
the rule diverged once before, and each divergence either salvaged a payload the model
never nominated or discarded a usable review.

The rule: exactly one complete JSON root, which must be the whole answer. Prose before
or after it is tolerated. JSON *syntax* outside it is not, because it makes the answer
ambiguous. Specifically refused:

- Two complete roots, where picking either is a guess.
- A root beside or inside malformed JSON, parseable or not — `{"outer":{"findings":[]}
  BROKEN`, `{"a": nope} {"findings":[]}`, `} prose {"findings":[]}`.
- Any `{` or `[` outside the payload, and any `}` or `]` *before* it.

Two exceptions, each evidence-backed rather than convenience:

- A simple bracketed prose label such as `[draft 1]`, covered by
  `test_extracts_object_after_bracketed_preamble`.
- An unmatched closer *after* a complete payload. It cannot mean the payload is an
  interior fragment — that requires an enclosing opener before it, which is refused —
  so what remains is trailing model noise. Observed from a live reviewer and covered by
  `test_loads_critique_array_before_unrelated_trailing_bracket`, added from phase-5
  acceptance output.

Reasoning parts are never eligible as answer text at either layer, so no rule above can
be reached by a scratchpad.

Whether structured output or the text fallback produced the batch must be reported
honestly in the job log, using the same two wordings the shared runner emits. The
rollout canary treats `used structured_output` as evidence that the transport worked,
so the fallback must never claim it.

No `small_model`, title-model variable, reviewer-config key, model override, or
provider change is permitted for this purpose. `reviewers.opencode.model` and
`AI_REVIEW_OPENCODE_MODEL` remain the sole OpenCode model controls.

## Scope

**In scope**

- The loopback `serve` client, its stage schema transport, and its session/permission
  request contract.
- Answer-text extraction across every reviewer: only parts whose `type` is `text` (or
  an untyped envelope) are answer text. A response containing only reasoning or tool
  parts is a model error naming that fact, never a schema error.
- One complete JSON root per adapter response, per Layer 2 above: brace-free prose
  around the payload stays tolerated, because refusing it converts usable reviews into
  schema errors across all four adapters; any other JSON syntax outside the payload is
  refused, because accepting it yields a review whose provenance is a guess.
- Full redacted adapter stdout persisted on parse failure, and newline structure
  preserved in the bounded preview, so a stream failure is readable rather than
  inferred.
- An image-build guard requiring the pinned server CLI to expose both loopback listen
  flags.
- The `opencode-ai` pin advanced to the exact reviewed version used by the client.
- A refresh of the remaining reviewer CLI pins — `@anthropic-ai/claude-code`,
  `@openai/codex`, and `cursor-agent` — carried in the same change by owner decision.
  Only the `opencode-ai` pin is load-bearing for the transport above. The acceptance
  canary therefore moves four reviewer versions at once: if it regresses on a reviewer
  other than OpenCode, attribute the regression to its pin before this
  specification's transport change, and re-check against the previous pin set.

**Explicitly out of scope**

- Model-default changes, model/provider overrides, or a `small_model`/title-model
  setting.
- Ripgrep availability for OpenCode's `grep` tool. The same job shows
  `"ripgrep execution failed"`, because no image installs ripgrep. That is a real
  defect, but enabling a search tool that has never run changes what the reviewer does
  and would confound this specification's canary. It also depends on an unresolved
  question — whether OpenCode's `grep`/`read` are confined to the session directory,
  given the failed call targeted the live CI checkout rather than the sanitized review
  root — which must be answered before ripgrep is installed.
- Image publication, template repinning, immutable digest updates, release-input
  changes beyond the recipe/fixture hashes, or release-history changes.

## Considered and rejected

- **An OpenCode-only text rule stricter than the shared one** — requiring the whole
  answer text to be the payload, with no surrounding prose. Adopted in an earlier
  revision on the reasoning that a schema-enforcing transport justifies a higher bar for
  its fallback, then reverted: once the shared rule refuses all JSON syntax outside the
  payload, the extra strictness buys no safety and only converts a recoverable review
  into a zero-finding panel — the precise outcome this specification exists to prevent.
  One rule, one implementation.
- **Tolerating balanced-but-unparseable JSON adjacent to the payload**, on the grounds
  that prose sometimes mentions JSON shapes. Rejected: it leaves the salvage hazard open
  for any malformed structure whose braces happen to balance, which is the same class of
  defect as the nested case, and the prose shapes it protects were hypothetical while
  the hazard is demonstrable. Brace-mentioning prose fails closed with a schema error.
- **Refusing unmatched closers on both sides of the payload**, for symmetry. Rejected
  for the trailing side only, on the evidence and reasoning recorded above; refusing it
  would regress a live-output case the suite has covered since phase 5.

## Acceptance criteria

- A fixture reproducing job 2624957 — reasoning and tool parts, no answer part —
  yields a model error naming the missing answer part, and never adopts the batch that
  existed only in the scratchpad.
- API-contract tests exercise the real client against a server stand-in that rejects
  unrecognized request keys, so a drift in the session path, directory header,
  permission rules, or `format` body fails the suite. A stand-in *client* asserting its
  own request shape does not satisfy this criterion.
- One extractor governs every adapter *and* the OpenCode text fallback; there is no
  per-adapter variant. Brace-free prose around a single complete payload parses, as do
  the two documented exceptions. Refused: two complete roots, and a payload accompanied
  by any other JSON syntax — including balanced-but-unparseable JSON, which an earlier
  revision accepted because it only checked for containers left open, and a leading
  unmatched closer, which a later revision accepted because it only scanned openers.
- The text fallback logs `carried no structured_output` and never
  `used structured_output`, so the canary evidence below cannot be satisfied by the
  degraded path.
- A parse failure writes the complete redacted stdout with its line structure intact;
  a successful run writes neither debug artifact.
- The reviewer-image build fails if the pinned OpenCode CLI no longer supports the
  loopback `serve --hostname` and `serve --port` flags.
- Targeted adapter tests, documentation checks, full quality, and the pull-request
  image build/preflight pass without credentials or a real OpenRouter request.
- Real OpenRouter verification is deferred to rollout: a canary must show
  `status: success`, `raw_finding_count > 0`, and the `used structured_output` log
  line. `grep` is still expected to fail there until the ripgrep follow-up lands.
