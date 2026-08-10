# SPEC-52 — Normalize stringified structured reviewer output at the shared boundary

- **Status:** Proposed (post-1.0). A first, narrower fix for one shape of this quirk
  landed in [PR #116](https://github.com/seanleecoder/code-tribunal/pull/116) and shipped
  in 1.0.2; this specification generalizes it and moves it to the boundary every reviewer
  seat shares.
- **Classification:** S / reviewer-transport correctness.
- **Severity:** Medium. Every gap below fails closed — the effect is reduced review
  coverage (a dropped finding, or a critic seat recorded as `schema_error`), never
  malformed output accepted into the panel.
- **Effort:** S.
- **Depends on:** [SPEC-50](spec-50-opencode-structured-reviewer-output.md), which
  established the OpenCode structured-output transport, the rule that the client is the
  sole normalizer emitting the reviewer batch onto the shared `findings`/`critiques`
  root, and the single shared answer-text extractor.

## Incident and forensic rationale

A live OpenRouter review with `google/gemini-3.5-flash-lite` returned a schema-backed
object whose `findings` array carried a **string** rather than an object — the string
itself being one complete, unambiguous JSON finding:

~~~json
{"findings": ["{\"anchor\": {…}, \"severity\": \"minor\", …}"]}
~~~

The shared finalizer treats a non-dict finding as malformed and drops it
(`ai-review/src/ai_review/schema.py:435`), so a usable finding was lost to a transport
artifact. PR #116 added `_decode_stringified_structured_items` in
`ai-review/src/ai_review/opencode_client.py:320`, which decodes exactly that case — a
string item that loads, through the duplicate-key-rejecting loader, to a dict — and
leaves prose, scalars, arrays, and malformed strings untouched so they still fail closed.

That fix is correct. It is also placed too narrowly and covers only one of the shapes the
quirk can take. Four gaps remain, each reproducible from the code as it stands on `main`:

1. **List-root structured payloads bypass decoding.** `_normalize_message` explicitly
   admits `structured` as a list (`opencode_client.py:385`), and
   `adapter_runner._coerce_adapter_root` turns a bare list into `{"critiques": [...]}`
   for the critique stage (`adapter_runner.py:271-282`). The decoder's
   `not isinstance(payload, dict)` early return (`opencode_client.py:333`) therefore
   skips a supported critique root entirely: `["{\"verdict\": \"agree\", …}"]` keeps its
   string items and `finalize_critique_batch` raises, costing the whole critic seat.
2. **The text fallback is not normalized.** The degraded path at
   `opencode_client.py:403` returns its payload without passing through the decoder, so
   `{"findings": ["{…}"]}` recovered from answer text still loses the finding — same
   provider, same quirk, different branch.
3. **A whole stringified array is not handled.** The adjacent shape
   `{"findings": "[{…}]"}` — stringified at the array boundary rather than the item
   boundary — passes through untouched and fails as
   `adapter output findings must be an array`, taking the entire reviewer seat to
   `schema_error` rather than dropping one item.
4. **The fix lives at one adapter's boundary.** The root cause is provider and
   schema-transport behavior, not adapter behavior. The same model can be configured for
   the codex, gemini, claude, and cursor CLI seats, whose `structured_output` flows
   through `_coerce_adapter_root` with no decoding at all.

On the fourth point, be precise about the evidence: the shared runner **demonstrably**
has the same blind spot for every seat, but no transport other than OpenCode over
OpenRouter has been **observed** emitting this quirk. The shared placement is a
blind-spot argument, not a reproduced second failure. It is adopted because a
transport-layer artifact belongs at the transport-layer boundary and because the
consolidation removes an adapter-specific branch — which is SPEC-50's stated doctrine —
not because a second seat has failed.

## Non-negotiable decision

Reviewer output passes through **one stage-aware normalization step in the shared
runner**, applied after root coercion, reached by the structured and text-fallback paths
alike. The adapter-specific decoder is removed in the same change.

- **Placement.** The step runs inside, or immediately after, `_coerce_adapter_root`
  (`ai-review/src/ai_review/adapter_runner.py:268`) — the single point every seat funnels
  through, from all five call sites (`adapter_runner.py:421`, `:458`, `:470`, `:485`,
  `:519`), *including* OpenCode, whose client emits the reviewer batch straight onto the
  shared `findings`/`critiques` root (see the comment at `adapter_runner.py:476`). Order
  matters: coerce first, so a bare list root has already become `{"critiques": [...]}`,
  then decode the resulting object. That ordering is what closes gaps 1 and 2 together
  rather than one at a time.
- **Stage-aware only.** `review` normalizes `findings`; `critique` normalizes
  `critiques`. A `None` stage is a passthrough. Every runtime call site passes an
  explicit stage, so inferring the key from the payload would grant the step reach the
  pipeline never needs.
- **Per-item rule.** A `str` item that loads, via `json_loads_no_duplicates` with its
  duplicate-key rejection preserved, to a **dict** is replaced by that dict. Everything
  else — prose, scalars, nested arrays, malformed JSON, and a string that decodes to
  another string — is left exactly as it arrived and fails closed downstream.
- **Whole-array rule.** A `str` **value** for `findings`/`critiques` that loads to a
  **list** is replaced by that list, after which the per-item rule runs over its
  contents. This closes gap 3.
- **One pass, no recursion.** A double-encoded item decodes to a `str` and is left alone.
  This is deliberate and must be stated in the implementation's docstring; PR #116's
  wording only implied it.
- **Copy-on-write.** The step returns the original object unchanged unless something
  actually decoded. PR #116's `changed` short-circuit is preserved.
- **Fail-closed is unchanged.** Decoding is a transport concern and never grants
  admission. A string that decodes to a well-formed but non-reviewer object is still
  refused by schema validation exactly as an inline non-reviewer object would be. This
  boundary is what keeps the step from becoming a laundering path for arbitrary JSON.
- **Envelopes are untouched.** A root carrying neither `findings` nor `critiques` — a
  Claude result envelope, a stream event — passes through unmodified, and a root that is
  neither object nor array still raises
  `SchemaValidationError("adapter output root must be an object")`.
- **The adapter-specific decoder is deleted.**
  `opencode_client._decode_stringified_structured_items`
  (`ai-review/src/ai_review/opencode_client.py:320-356`) and its call site at `:389` are
  removed. `_normalize_message` keeps its `stage` parameter: the shared extractor call
  `extract_json_text(text, stage=stage)` at `:403` still requires it.
- **The decode is reported.** Each coercion that changes something writes one line to
  stderr, through `redact_text`, in the voice of its neighbours at
  `ai-review/src/ai_review/schema.py:439` and `:451`:

  ~~~text
  ai-review: <stage> decoded N stringified structured item(s)
  ~~~

  Every other step in this pipeline that reshapes adapter output reports that it did.
  A silent workaround becomes invisible load-bearing code: nothing would show how often
  the quirk fires, and nothing would show when the provider stops emitting it and the
  step becomes dead weight.

## Scope

**In scope**

- The shared normalization step, its placement relative to root coercion, and its stderr
  reporting.
- Deleting the OpenCode-specific decoder and its call site.
- Test coverage for every shape named in the acceptance criteria.

**Explicitly out of scope**

- Any change to the reviewer model or provider pins. This specification is a
  tolerant-reader fix at the boundary; the upstream cause stays upstream.
- Retrying or re-prompting the model when the quirk fires. The decode is deterministic
  and local; a retry spends a model call to work around a transport artifact already
  recoverable without one.
- Loosening `finalize_critique_batch` — see below.

## Considered and rejected

- **Making `finalize_critique_batch` drop malformed critiques per item.** The asymmetry
  is real and was raised during the review of PR #116: a malformed *finding* is dropped
  per item (`schema.py:435`), while a single non-dict *critique* raises
  `SchemaValidationError` (`schema.py:196`) and discards the **entire** critic batch. It
  is a genuine amplifier — any stringified critique this specification fails to catch
  costs every critique from that critic, not one — but it is an amplifier of the missed
  normalization paths rather than an independent defect, and the fix is not free.
  `schemas/critique_batch.schema.json` is `additionalProperties: false` and carries no
  drop counters, unlike the finding batch's `raw_finding_count` /
  `accepted_finding_count` / `dropped_finding_count`. Dropping critiques per item today
  would therefore silently shrink a critic's contribution to consensus with nothing in
  the artifact recording that it happened — trading a loud, legible failure for a quiet
  one. Doing it properly requires explicit per-item critique quality accounting and a
  schema version bump, which is its own specification. Fix the normalization paths first;
  revisit only if a stringified critique still slips through afterwards.
- **Recursive decoding of multiply-encoded items.** Rejected on the same doctrine that
  governs the shared text extractor: decode only the exact, unambiguous case that has
  been observed. A second level of encoding has never been seen, and each additional
  level widens what the step will accept without widening what it can verify.
- **Inferring the stage from the payload when `stage is None`.** Rejected as reach the
  pipeline does not need. Every runtime call site passes an explicit stage; a payload
  that arrives without one is not a reviewer batch this step should be reshaping.
- **Leaving the fix in the OpenCode client and duplicating it on the text-fallback
  branch.** Rejected: it fixes gap 2 while leaving gaps 1 and 4 open, and it grows a
  second copy of a rule SPEC-50 requires to exist exactly once.

## Acceptance criteria

- A list-root structured payload of stringified critiques is decoded and finalizes
  successfully, where today it raises and discards the critic batch.
- A string that decodes to a well-formed but **non-reviewer** object still fails closed
  at finalize. This is the guard that keeps the step from laundering arbitrary JSON, and
  it must be covered explicitly.
- A whole stringified array, `{"findings": "[{…}]"}`, is decoded and its items normalized.
- The OpenCode text-fallback branch decodes a stringified item, so the degraded path no
  longer loses a recoverable finding.
- A double-encoded item remains a string and is dropped by the finalizer, pinning the
  one-pass rule.
- A non-OpenCode seat's `structured_output` receives the same decoding, pinning the
  shared placement.
- A `None` stage passes through unmodified.
- The two tests added by PR #116 —
  `ai-review/tests/unit/test_opencode_client.py:36` and `:93` — stay green, now
  exercising the shared path rather than the deleted adapter-specific decoder.
- A run in which nothing decodes writes no decode line; a run in which items decode
  writes exactly one line naming the stage and the count.
- Targeted adapter tests, documentation checks, and full `make quality` pass without
  credentials and without any real provider request.

Files an implementer is expected to touch:

- `ai-review/src/ai_review/adapter_runner.py` (the shared step)
- `ai-review/src/ai_review/opencode_client.py` (deletion)
- `ai-review/tests/unit/test_adapter_runner.py`
- `ai-review/tests/unit/test_opencode_client.py`

No schema, image, pin, or release-input change is required or permitted for this purpose.
