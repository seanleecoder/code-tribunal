# Evidence record: GitHub / real default-model panel smoke / 2026-08-10

Status: passed

Release-runtime-source: 54dffa130be5c921602f264a2123fda4b1895f13
Release-base-digest: sha256:960600d339a9c7ed95539fe5de6f2414ed82fb06b96a02ed267d9332cd3d7fb4
Release-reviewer-digest: sha256:6bf8fdfbe11a3b85519ae954411b436e5bed5f895e900074404a7b27359e6fab

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

This is the single real-provider campaign for the final 1.0.2 runtime. It doubles
as the default-model smoke; no separate token-spend campaign was run.

## Identity

- Platform: GitHub Actions (github.com), same-repository pull request
- Date/time: 2026-08-10, 08:37:13–08:42:05 UTC
- Consumer project: `seanleecoder/code-tribunal-demo` (operator-controlled scratch,
  see [consumer projects](CONSUMER-PROJECTS.md))
- Change request: PR #14, branch `evidence/chain-a-54dffa1`
- Workflow run: `31370873644`
- Consumer head: `b8b6737b0c528b7023f26f827951c8910b68e4c1`
- Runtime source: `54dffa130be5c921602f264a2123fda4b1895f13`
- Base image: `1.0-54dffa130be5c921602f264a2123fda4b1895f13@sha256:960600d3…`
- Reviewer image: `1.0-54dffa130be5c921602f264a2123fda4b1895f13@sha256:6bf8fdfb…`

## Preconditions

- The candidate workflow was first adopted by zero-token PR #13. All temporary
  mock and `AI_REVIEW_REQUIRE_REAL_*` variables were then deleted and confirmed
  absent before PR #14 was opened, restoring the workflow's fail-closed
  `AI_REVIEW_REQUIRE_REAL_*=1` defaults.
- No model or effort override was set. Claude, Codex, and OpenCode were enabled;
  Cursor remained disabled pending a pinned Composer model.
- `gate` is a required status check through the active default-branch ruleset.
- The bounded fixture added `src/session.py` with MD5 token derivation and a direct
  equality comparison, giving the panel genuine security defects to review.

## Actual result

Resolved default models, each with no override:

| Seat | Status | Model | raw | accepted | dropped | usable |
|---|---|---|---:|---:|---:|---|
| claude | success | `anthropic/claude-haiku-4.5` | 3 | 3 | 0 | true |
| codex | success | `openai/gpt-5.6-luna` | 1 | 1 | 0 | true |
| opencode | success | `google/gemini-3.5-flash-lite` | 0 | 0 | 0 | true |
| cursor | skipped | `auto` (disabled) | 0 | 0 | 0 | false |

- Consensus: `panel_status: full`; successful and resolution-eligible reviewers
  were `[claude, codex, opencode]`; `failed_reviewers: []`;
  `panel_convergence: 0.3333333333333333`; `surface_count: 3`;
  `drop_count: 0`; `fyi_count: 0`; `block_merge: true`.
- Three findings surfaced: broken MD5 token derivation, deterministic session
  tokens, and a timing-attack-prone comparison. The MD5 finding was a blocker
  corroborated by Claude and Codex.
- `post`: success, three discussions created (`3747918108`, `3747918179`, and
  `3747918250`), no warnings, head binding exact.
- `gate`: exit 7. Every review, critique, consensus, and posting job passed; the
  required `gate` check failed because consensus contained a blocker. This is the
  expected fail-closed product result, so the overall workflow conclusion is
  `failure` while the evidence row passes.

## Release-blocker closure

The superseded `f21418f…` campaign remains visible on demo PR #12, run
`31367545101`, attempts 1 and 2. Both attempts reached the real OpenCode default
model but returned one string item inside the schema-backed `findings` array. The
shared finalizer dropped it with `string indices must be integers, not 'str'`, so
OpenCode was not resolution-eligible and the panel was degraded.

Code Tribunal PR #116 added a narrow OpenCode-client normalization: decode only an
exact stringified JSON object, using the duplicate-key-rejecting loader; prose,
arrays, scalars, malformed JSON, and duplicate-key objects remain on the fail-closed
path. Focused tests passed (`61 passed`, `41 subtests`), the full suite passed
(`889 passed`, `2 skipped`), and the rebuilt reviewer image passed its packaged
OpenCode structured-output preflight. The final live run returned a valid empty
OpenCode batch, so it proves the real default route and full-panel eligibility but
does not independently exercise the new string-decoding branch against a provider
response; that exact branch is established by the checked-in regression test.

## Audit

- Downloaded artifacts: inputs, all review and critique batches/status files,
  consensus, and post result from run `31370873644` (33 files, about 0.1 MB).
- `scripts/scan_evidence_leaks.py` reported no credential material across those
  downloaded files with ten pattern/entropy detectors.
- An exact-value scan was not performed because configured secret values were not
  exported from GitHub. No claim of exact-value absence is made.
- Known unexercised paths: Cursor; non-default effort routes; OpenRouter token/cost
  accounting; GitLab behavior.

## Verdict

Scoped pass for GitHub Actions on the public scratch consumer at runtime source
`54dffa1` and the recorded immutable image pair. The three enabled default models
resolved at the real provider, all three reviewers were resolution-eligible, the
panel reached full consensus, real findings posted, and the required check blocked
the merge. It does not establish Cursor, non-default effort, cost, or GitLab
behavior.
