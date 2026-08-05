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
  that version, and lowercase SHA-256 digests that are not the all-zero
  placeholder — the contract `cursor-agent.pin` already carries, plus a second
  digest for the extracted binary, because what must be trustworthy at review time
  is the file that resolves on `PATH`, not the archive that was downloaded during
  the build.
- The pinned ripgrep version must equal the version the pinned `opencode-ai` would
  otherwise fetch, so the reviewer runs the ripgrep OpenCode was tested against.
  The pin must name that `opencode-ai` version, and the pin check must enforce the
  equality: a bump of either alone is a failure, not a silent mismatch.
- A review-time ripgrep download must fail the review. The pinned binary exists so
  that fetch never happens; if it happens, an unverified executable ran inside the
  reviewer and its findings must not be posted.

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
- A publication-gating image preflight that reads the effective permissions out of
  OpenCode's own resolver, and a review-time failure on a ripgrep download.
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
  while `read`, `glob`, and `grep` stay enabled. This is asserted mechanically by
  the image preflight against a config captured from a real adapter run, not
  restated in the probe: session-create returning an id proves only that the server
  tolerated the rule's shape, since a server that ignored an unknown permission key
  would answer identically.
- The preflight gates merge as well as publication — it is a step in the image build
  job with no event condition, because a separate job could only consume the image
  artifact, which pull requests do not upload, and would skip exactly the runs where
  a pin or adapter change is under review. It runs with egress denied so a restored
  review-time download cannot pass it.
- The adapter must resolve the pinned CLIs from `/usr/local/bin` before consulting the
  ambient PATH. `OPENCODE_BIN` (and the interpreter) are resolved before `env -i` and
  forwarded into it, so an ambient-first lookup would let a binary earlier on the
  runner's PATH substitute itself for the pinned one — the substitution the fixed
  trusted PATH exists to prevent. The preflight proves this against a real decoy.
  Resolution must also come *before* the CLI-availability gate and go by absolute
  path, so a PATH that omits `/usr/local/bin` can neither hide the pinned binary nor
  make the gate reject it. In the packaged image — recognized by the adapter running
  from `/opt/ai-review/adapters`, the copy the base image installed — a missing pinned
  CLI is a broken image and must fail rather than fall back to an ambient binary;
  ambient resolution remains a fallback only for checkouts and dev machines.
- A review-time ripgrep fetch must be recognized as the server logs it, not by
  scanning a retained buffer afterwards. The buffer is deliberately bounded for
  diagnostics, so a fetch early in a real review is evicted long before the check
  runs; a bounded buffer cannot be the substrate for the verdict.
- `scripts/check_supply_chain_pins.py` fails on a missing, malformed, off-host, or
  placeholder `ripgrep.pin`, on a `binary_sha256` copied from the tarball digest, on
  an `opencode_version` that disagrees with `package.json`, and on a
  `reviewer.Dockerfile` that does not install the pin onto `PATH`, download the
  pinned URL, or assert both digests.
- The reviewer-image build fails if either pinned digest does not match, if `rg`
  does not resolve to `/usr/local/bin/rg`, or if its version differs from the pin.
- `ai_review.opencode_client` fails the review if the server log shows a ripgrep
  download.
- The session-create rules are asserted to be **retained**, not merely accepted: the
  probe reads the session back and requires the `external_directory` deny to still be
  present, so a server that dropped the unrecognized key fails.
- Live acceptance is deferred to rollout: a canary must show a `grep` tool call with
  `status != "error"` and a non-empty result, and no ripgrep-download line in the
  adapter log. The non-empty requirement is load-bearing: if the review root's path
  differs from its realpath, every in-root path can read as external and the
  reviewer is denied wholesale, which an error-free-call check alone would accept.

**Not achievable provider-free, and why.** A tool-layer allow/deny decision cannot be
observed through the pinned server's API. `/experimental/tool` and
`/experimental/tool/ids` are `GET` listings; the only tool-ish `POST` is
`/session/{id}/shell`, which is the bash tool rather than `read`/`grep`; and
`POST /api/session/{id}/permission` — which returns `{id, effect}` and looks like the
decision oracle — disagrees with the tool layer: loaded with the adapter's real config
it answers `deny` for every action, including `read`/`glob`/`grep` on absolute in-root
paths that `debug agent` resolves to `allow`, because it resolves against the top-level
`"*": "deny"` instead of the agent's allows. A probe built on it would assert the
reviewer cannot read its own review root, which is false. Forcing a real `grep` call
needs a model, i.e. a provider.

Possible future work, not required here: serve an OpenAI-compatible stub model on
loopback from the preflight, have it return a `grep` tool call on an external absolute
path, and assert the tool is refused. That would give end-to-end tool-layer evidence
without a real provider, at the cost of coupling a publication gate to the pinned
CLI's provider-SDK wire format. Until then, OpenCode's own resolver is the
provider-free oracle and the live canary is the end-to-end one.
