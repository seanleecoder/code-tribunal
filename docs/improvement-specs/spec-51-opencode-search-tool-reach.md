# SPEC-51 — Bound and supply the OpenCode search tools

- **Status:** Implemented (post-1.0; runtime source and image recipe; immutable image publication and live-provider acceptance remain separate rollout work).
- **Classification:** S / reviewer isolation and supply-chain correctness.
- **Severity:** High (two independent defects: an unverified binary fetched and executed at review time, and a filesystem boundary that held only by accident).
- **Effort:** S.
- **Depends on:** [SPEC-50](spec-50-opencode-structured-reviewer-output.md), which introduced the loopback session client this change extends.
- **Related work:** the snapshot-containment contract in
  [SPEC-19](../history/specs/spec-19-opencode-reviewer-optimization.md), whose
  sanitized review root this specification makes actually binding.

## Incident and forensic rationale

GitLab job 2624957 logged, alongside the schema failure SPEC-50 addresses:

~~~json
{"type":"tool_use","part":{"tool":"grep","state":{"status":"error",
 "input":{"pattern":"NEXT_PUBLIC_CONTENTPASS","path":"/builds/burda_style/head/packages"},
 "error":"ripgrep execution failed"}}}
~~~

Two facts follow from it, both verified against the pinned `opencode-ai` 1.18.12.

**No image installed ripgrep.** OpenCode's `grep`/`glob` tools resolve their
binary as `which("rg")` → `$HOME/.cache/opencode/bin/rg` → download
`ripgrep-<version>-<platform>` from GitHub releases, extract, `chmod 0755`, exec.
The download checks only for a non-zero byte length; there is no checksum. The
adapter gives each run a fresh `HOME`, so the cache is always cold and every
review would attempt that fetch. `base.Dockerfile` installed `ca-certificates git`
and `reviewer.Dockerfile` installed `ca-certificates curl tar`; ripgrep appeared in
no recipe, adapter, or document. The tool had therefore never worked, and
`"grep": "allow"` had been declared for a tool that could not run since SPEC-19.

**The requested path was outside the review root.** The call targeted
`/builds/burda_style/head/packages` — the live CI checkout — not
`$AI_REVIEW_OUTPUT_DIR/.tmp/opencode-review-root.<pid>`. The adapter copies and
sanitizes the snapshot precisely so an MR cannot steer the reviewer through its
own `opencode.json`, `AGENTS.md`, or `.opencode/`. Those files exist unsanitized in
the checkout. ripgrep's absence was the only reason they were not reachable.

Reach is gated by OpenCode's `external_directory` permission. It is a permission
key of its own, so the adapter's `"*": "deny"` tool wildcard never covered it, and
the default is `{"*": "ask"}`. Verified against the adapter's exact generated
config with `opencode --pure debug agent ai-reviewer`:

| config | effective action for an external absolute path |
| --- | --- |
| as shipped before this change | `ask` |
| with `"external_directory": {"*": "deny"}` | `deny` |

`ask` in a headless reviewer is not containment. It is an approval request no
participant can answer, so the outcome is a stalled session rather than a refusal.

## Non-negotiable decision

- The adapter must deny `external_directory` explicitly, in both the agent and
  top-level permission blocks, and `ai_review.opencode_client` must repeat the
  denial in its session-create request. Relying on the tool wildcard, on a default,
  or on a missing binary is not containment.
- The reviewer image must ship a pinned, checksum-verified ripgrep on `PATH`, so
  OpenCode's resolution stops at `which("rg")` and no review-time download of an
  unverified executable can occur.
- `ripgrep.pin` must record an exact version, the upstream release URL containing
  that version, and a lowercase SHA-256 digest that is not the all-zero
  placeholder — the same contract `cursor-agent.pin` already carries.
- The pinned ripgrep version must equal the version the pinned `opencode-ai` would
  otherwise fetch, so the reviewer runs the ripgrep OpenCode was tested against.

## Scope

**In scope**

- `external_directory: {"*": "deny"}` in the adapter's generated config and in the
  client's session permission rules.
- `ai-review/images/ripgrep.pin`, a verifying builder stage in
  `reviewer.Dockerfile`, and the copy onto `/usr/local/bin/rg`.
- Build guards proving `rg` resolves to the pinned path under the adapter's
  forwarded `PATH` and reports the pinned version — presence somewhere in the image
  is not sufficient.
- `ripgrep.pin` validation in `scripts/check_supply_chain_pins.py`, and a
  `reviewer.Dockerfile` assertion that the pin is installed onto `PATH`.
- Documentation of the reviewer's bounded filesystem reach.

**Explicitly out of scope**

- Widening or narrowing `read`, `glob`, or `grep` inside the review root. Those
  remain allowed; only reach beyond the root changes.
- Any configuration surface for the reviewer's filesystem reach. There must be no
  project-controlled way to widen it.
- Image publication, template repinning, or immutable digest updates.

## Acceptance criteria

- Fake-adapter tests assert `external_directory: {"*": "deny"}` in both permission
  blocks of the generated config and in the session-create request body.
- `opencode --pure debug agent ai-reviewer`, run against the adapter's generated
  config, resolves `external_directory` for an external absolute path to `deny`
  while `read`, `glob`, and `grep` stay enabled.
- `scripts/check_supply_chain_pins.py` fails on a missing, malformed, off-host, or
  placeholder `ripgrep.pin`, and on a `reviewer.Dockerfile` that does not install
  it onto `PATH`.
- The reviewer-image build fails if the pinned artifact's checksum does not match,
  if `rg` does not resolve to `/usr/local/bin/rg`, or if its version differs from
  the pin.
- Live acceptance is deferred to rollout: a canary must show a `grep` tool call
  with `status != "error"`, and no `downloading ripgrep` line in the adapter log.
