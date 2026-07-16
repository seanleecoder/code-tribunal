# SPEC-21 — Cursor CLI as an opt-in substitute reviewer

- **Severity:** Medium (capability/flexibility) · **Effort:** M · **ROI rank:** n/a (post-Phase-3)
- **Depends on:** none hard. SPEC-20 (usage accounting) recommended first so
  the panel's cost story is symmetric when cursor lands (cursor's `usage` is
  `null` — see Notes).

## Why

Operators want the option to swap the opencode seat for Cursor's CLI agent and
its first-party Composer model.

**Constraint discovered up front (research verdict, 2026-07): the Cursor CLI
cannot route through OpenRouter.** BYOK/custom-base-URL is an IDE-only feature
that excludes agent mode, and the CLI is agent-only; all CLI inference goes
through Cursor's backend, authenticated by a Cursor account key
(`CURSOR_API_KEY`) and billed through the Cursor plan. There is no flag or
config for an OpenRouter key or custom endpoint. Consequences this spec must
encode, not hide:

- The reviewer panel's "single OpenRouter egress boundary" invariant gets a
  **deliberate, opt-in exception**: an enabled cursor reviewer sends the
  rendered prompt (including the MR diff and repo snapshot content it reads)
  to Cursor's backend. This must be documented in config comments,
  `SUPPLY_CHAIN.md`, and the README, and gated behind `enabled: false`.
- Endpoint pinning à la `OPENROUTER_BASE_URL`/`ANTHROPIC_BASE_URL` validation
  is not possible (no endpoint env exists to pin); compensating controls are
  the dedicated credential (`CURSOR_API_KEY` — the OpenRouter key must never
  enter cursor's environment) and the `env -i` scrub.

Verified CLI facts (Cursor docs/changelog, 2026-07): binary `cursor-agent`
(renamed to `agent` with `cursor-agent` kept as alias, Jan 2026); headless
print mode `-p` with `--output-format text|json|stream-json`; stdin piping
works in print mode; `--model <id>` selects any model on the account's plan
including Composer (current id to be verified at implementation time via
`cursor-agent models`; "composer" line, e.g. `composer-2.5`); auth via
`CURSOR_API_KEY` env (service-account keys available for teams); permissions
config file supports `permissions.allow`/`permissions.deny` rules like
`Write(**)` and `Shell(*)`; **no native timeout flag**; **no token-usage
fields in CLI output** (open upstream feature request); install is
`curl https://cursor.com/install | bash` (not npm), dropping the binary under
`~/.cursor/bin`.

**Runtime sandbox tradeoff discovered during implementation:** the pinned CLI's
kernel sandbox cannot initialize inside nested GitHub Actions job containers.
The shipped adapter selects the pinned CLI's native read-only Q&A mode without
mutating persistent CLI state by running
`-p --mode ask --sandbox disabled --trust` in a disposable home, with
`cli-config.json` allowing `Read(**)` and denying `Write(**)`, `Write(/**)`,
and `Shell(*)`.
This is a weaker CLI-policy boundary, not a claim of kernel isolation. A real
pinned-image smoke test must demonstrate that hostile write and shell requests
have no side effects before an operator enables Cursor. Unit tests verify that
`Shell(*)` is consistently configured but cannot prove how the pinned CLI's
glob engine interprets it; the trusted-main smoke remains the enablement gate.
Re-evaluate
`--sandbox enabled` whenever the pinned CLI changes in case a future release
supports kernel isolation in nested containers.

The `--output-format json` result envelope is
`{"type": "result", "subtype": ..., "is_error": bool, "result": "<text>",
"session_id": ..., "duration_ms": ..., ...}` — which the existing runner
already parses: `_load_adapter_json` unwraps a string `result` field and
`_is_adapter_error_event` handles `is_error`. **No parser changes are
needed.**

## Scope

- **In:**
  - new `ai-review/adapters/cursor.sh`;
  - `ai-review/config/review.yaml`: new `reviewers.cursor` entry
    (`enabled: false`);
  - `ai-review/src/ai_review/adapter_runner.py`: add
    `AI_REVIEW_REQUIRE_REAL_CURSOR` to `_AI_REVIEW_ADAPTER_CONTROLS`; add a
    `cursor` branch to `_cli_reviewer_validation_error`;
  - CI templates `ai-review/ci/review.gitlab-ci.yml` and
    `ai-review/ci/review.github-actions.yml`;
  - `ai-review/images/reviewer.Dockerfile` + new pin file
    `ai-review/images/cursor-agent.pin`; `ai-review/images/SUPPLY_CHAIN.md`;
  - docs: `ai-review/README.md` (runtime-overrides + substitution recipe);
  - tests (see Tests).
- **Out:**
  - `config.py` — env overrides (`AI_REVIEW_CURSOR_ENABLED/_MODEL/_EFFORT`)
    are reviewer-name-generic and work automatically once the config entry
    exists (verified);
  - `mock_reviewer.py` — reviewer-name-agnostic, works unmodified;
  - runner output parsing — existing result-envelope path covers cursor;
  - any default-enabled configuration; consensus/panel logic (see Notes for
    why panel math is unaffected).

## Implementation

### 1. `config/review.yaml`

```yaml
  cursor:
    enabled: false # Opt-in substitute for opencode. Override: AI_REVIEW_CURSOR_ENABLED
    adapter: adapters/cursor.sh
    model: composer # Cursor Composer model id (verify current id via `cursor-agent models`); override: AI_REVIEW_CURSOR_MODEL
    timeout_seconds: 600
    max_findings: 50
    credential_variable: CURSOR_API_KEY # Cursor account/service key — NOT OpenRouter
```

Comment block must state: Cursor CLI has no OpenRouter/BYOK route — enabling
this reviewer sends review inputs to Cursor's backend, a second egress
destination outside the OpenRouter boundary; substitution recipe is
`AI_REVIEW_CURSOR_ENABLED=true` + `AI_REVIEW_OPENCODE_ENABLED=false` + the
`CURSOR_API_KEY` secret.

Panel note: with cursor disabled (default) enabled_count stays 3; in the
substitute configuration it is also 3; if an operator only disables opencode
it is 2 — all satisfy `min_successful_reviewers_for_blocking: 2` and the
`votes_required: 2` quorum, so no panel config change is needed.

### 2. `adapter_runner.py`

- Add `"AI_REVIEW_REQUIRE_REAL_CURSOR"` to `_AI_REVIEW_ADAPTER_CONTROLS`.
- `_cli_reviewer_validation_error`: add a `cursor` branch that returns `None`
  after the shared `_MODEL_ID_RE` check, with a comment documenting that no
  endpoint env exists to pin for the cursor CLI and that Cursor's backend is
  an accepted, opt-in second egress destination gated by `enabled: false` and
  the dedicated `CURSOR_API_KEY` credential. (`_build_adapter_env` already
  injects only the reviewer's declared `credential_variable`, so the
  OpenRouter key never reaches cursor's env; cursor.sh's `env -i` enforces it
  again.)

### 3. `adapters/cursor.sh`

Mirror `opencode.sh` structure with `claude.sh`'s cd/absolute-path pattern:

1. **Mock/real gating** (opencode.sh pattern):
   `REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_CURSOR:-}"`; fall back to
   `"${PYTHON:-python3}" -m ai_review.mock_reviewer` when `AI_REVIEW_LOCAL_MOCK=1`
   (and not required-real), when `cursor-agent` is not on PATH, or when
   `CURSOR_API_KEY` is empty (exit 127 / exit 2 respectively when
   required-real). Require `AI_REVIEW_MODEL` and `AI_REVIEW_RENDERED_PROMPT`.
2. **Absolute prompt path** (claude.sh lines 33–36 pattern) — the adapter
   `cd`s into the review root before invoking the CLI.
3. **Clean review root** (`review` stage): copy
   `$AI_REVIEW_INPUT_DIR/repo_snapshot` into
   `$TMP_DIR/cursor-review-root.$$`, then strip MR-supplied steering config at
   **every level, files and symlinks**: `.cursorrules`, `.cursorignore`,
   `AGENTS.md`, `CLAUDE.md` (cursor reads both rule-file families), and
   `-name .cursor -prune` directories. Critique/respond stages: empty working
   root (opencode.sh parity — the prompt carries everything needed).
4. **Read-only enforcement**: redirect `HOME` to `$TMP_DIR/cursor-home` and
   write the CLI permissions config there
   (`$CURSOR_HOME_DIR/.cursor/cli-config.json` — verify exact filename/location
   against the pinned CLI):

   ```json
   {"permissions": {"allow": ["Read(**)"], "deny": ["Write(**)", "Write(/**)", "Shell(*)"]}}
   ```

   Deny wins over allow in cursor's model. If the pinned binary supports a
   `--sandbox` flag in print mode, pass it as well (verify; do not guess).
5. **Invocation** under scrubbed env, from the review root:

   ```sh
   cd "$CURSOR_REVIEW_ROOT"
   env -i \
     PATH="${PATH:-/usr/bin:/bin}" \
     TMPDIR="${TMPDIR:-/tmp}" \
     HOME="$CURSOR_HOME_DIR" \
     CURSOR_API_KEY="$CURSOR_API_KEY" \
     cursor-agent -p \
     --output-format json \
     --trust \
     --sandbox disabled \
     --mode ask \
     --model "$AI_REVIEW_MODEL" \
     < "$PROMPT_FILE"
   ```

   stdout is the single JSON result envelope; the runner's existing
   result-unwrap parses it. There is no `--json-schema`/`--output-schema`
   equivalent: the rendered prompt's JSON contract plus
   `finalize_finding_batch` + schema validation is the (already supported)
   conformance path — same as any reviewer whose structured-output steering is
   absent.
6. No timeout handling in the adapter — the runner's process-group kill at
   `timeout_seconds - 5` is the enforcement (cursor CLI has no timeout flag,
   and there are upstream reports of `-p` occasionally hanging, which the
   kill covers).

### 4. CI templates

- **GitLab (`ci/review.gitlab-ci.yml`)**: add `"AI review: [cursor]"` and
  `"AI critique: [cursor]"` extending the same templates as the opencode jobs,
  with `REVIEWER: cursor` and `AI_REVIEW_REQUIRE_REAL_CURSOR: "1"`; add both
  as `optional: true` needs where the other reviewers' jobs are listed
  (critique fan-in, consensus). Do **not** set `AI_REVIEW_CURSOR_ENABLED` in
  the template — `_env_flag` rejects an empty string, and unset means the
  yaml default (`false`) applies. Operators enable via project CI/CD
  variables (`AI_REVIEW_CURSOR_ENABLED=true`,
  `AI_REVIEW_OPENCODE_ENABLED=false`, secret `CURSOR_API_KEY`). A disabled
  cursor job costs one short `skipped` no-op (the runner exits 0 without
  spawning the adapter).
- **GitHub Actions (`ci/review.github-actions.yml`)**: extend both matrices
  to `[claude, codex, opencode, cursor]`; add to the review/critique job env:
  `CURSOR_API_KEY: ${{ vars.AI_REVIEW_CURSOR_ENABLED == 'true' && secrets.CURSOR_API_KEY || '' }}`,
  `AI_REVIEW_REQUIRE_REAL_CURSOR: "1"`, and
  `AI_REVIEW_CURSOR_ENABLED: ${{ vars.AI_REVIEW_CURSOR_ENABLED || 'false' }}`.
  Add the symmetric `AI_REVIEW_OPENCODE_ENABLED:
  ${{ vars.AI_REVIEW_OPENCODE_ENABLED || 'true' }}` so the substitution is a
  two-variable flip. The `|| '<default>'` guards are mandatory: an unset
  `vars.*` would otherwise materialize as `""`, which `_env_flag` rejects
  with a `ConfigError`.
  The matrix remains static by design: when Cursor is disabled, its review and
  critique entries exit before credential validation or adapter invocation and
  emit skipped artifacts. This costs two short no-op jobs but avoids duplicating
  runtime enablement logic in workflow graph conditions.

### 5. Docker image + supply chain

- Cursor's CLI is **not on npm** — it cannot join
  `images/package.json`/`package-lock.json` pinning. Add a builder stage to
  `images/reviewer.Dockerfile` that downloads a **specific versioned
  artifact** and verifies it against a repo-recorded SHA-256 in a new
  `ai-review/images/cursor-agent.pin` (version + download URL + sha256).
  Investigate at implementation time whether the installer supports a
  version-pinned URL scheme; if only a "latest" installer exists, run it in
  the builder stage, hash the produced binary, verify against the pin file,
  and document the weaker pin honestly in `SUPPLY_CHAIN.md`.
- Install to `/usr/local/cursor-agent/` and symlink
  `/usr/local/bin/cursor-agent` (plus `agent` alias only if nothing else
  claims it). The binary must not live under any `$HOME` path — adapters
  redirect `HOME` per run.
- Add a credential-free `cursor-agent --version` smoke line next to the
  existing `claude/codex/opencode --version` checks.
- `SUPPLY_CHAIN.md`: document the non-npm pin mechanism + refresh procedure,
  and add the egress-boundary paragraph (Cursor backend as opt-in second
  destination). If `scripts/check_supply_chain_pins.py` enumerates pinned
  inputs, extend it (and its unit test) to cover `cursor-agent.pin`.

### 6. Docs

`ai-review/README.md`: add cursor to the reviewer table/architecture notes
with the two caveats (no OpenRouter — Cursor billing/egress; no token usage in
CLI output → `usage: null` under SPEC-20, cost visible only in Cursor's
dashboard), plus the substitution recipe.

## Acceptance criteria

- Unit suite green; all cursor unit tests pass **without** the real binary
  (mock + fake-CLI harness).
- `run_adapter("cursor", "review")` with default config → status `skipped`,
  exit 0, no adapter spawn.
- With `AI_REVIEW_CURSOR_ENABLED=true` and the fake CLI: findings batch
  validates; recorded argv contains `-p`, `--output-format json`,
  `--model composer`; recorded cwd matches `cursor-review-root.\d+`; recorded
  tree contains the snapshot files but no `AGENTS.md`/`CLAUDE.md`/
  `.cursorrules`/`.cursorignore`/`.cursor`; recorded env contains
  `CURSOR_API_KEY` but **not** `OPENROUTER_API_KEY` (nor any other secret).
- Critique stage: fake CLI sees an empty working root.
- Invalid model id (e.g. containing quotes) → `model_error` status without
  spawning.
- Image build succeeds with the pinned cursor binary and the smoke line.
- One real run (`AI_REVIEW_REQUIRE_REAL_CURSOR=1`, real `CURSOR_API_KEY`,
  fixture MR) produces a valid finding batch within timeout — operator
  acceptance step, also confirms the exact Composer model id.
- A hostile real-image prompt asks Cursor to write a sentinel file and invoke a
  shell command; neither side effect exists after the run. Keep Cursor disabled
  in the consuming repository until this permission-denial check passes.

## Tests

- `tests/unit/test_openrouter_adapters.py` (extend the existing harness):
  - `_ENV_KEYS` += `CURSOR_API_KEY`, `AI_REVIEW_REQUIRE_REAL_CURSOR`,
    `AI_REVIEW_CURSOR_ENABLED`, `AI_REVIEW_CURSOR_MODEL`;
    `_MODEL_OVERRIDE_KEYS` += `AI_REVIEW_CURSOR_MODEL`.
  - `_write_fake_cli`: new `cursor-agent` branch — dump argv/env/cwd/tree
    (discoverable via the redirected `HOME` at `out/.tmp/cursor-home`,
    mirroring how the opencode fake traces via `OPENCODE_CONFIG_DIR`), read
    stdin fully, emit
    `{"type":"result","is_error":false,"result":"{\"findings\":[]}"}` so the
    test exercises the runner's real result-unwrap path.
  - `_write_inputs`: add `.cursorrules` and `.cursor/rules.md` (and a
    symlinked variant) to the snapshot fixtures for the stripping assertions.
  - New tests (each with `extra_env={"AI_REVIEW_CURSOR_ENABLED": "true"}`,
    which doubles as coverage for the enable-override path):
    real-path invocation; env isolation (extend the
    `test_cli_reviewer_env_is_isolated_from_unrelated_secrets` loop);
    `test_cursor_mock_fallback_produces_valid_batch`; disabled-by-default →
    `skipped` (no `extra_env`); critique empty root; invalid model format →
    `model_error`.
- `tests/unit/test_adapter_runner.py`: one explicit case —
  `{"type":"result","is_error":true,"result":"..."}` single-envelope →
  `AdapterModelError`/`model_error` (cursor error shape; likely already
  covered generically, make it explicit).
- `tests/unit/test_config_env_overrides.py`: `AI_REVIEW_CURSOR_ENABLED`
  true/false round-trip through `load_config`; cursor appears in
  `effective_config_summary`.
- `tests/unit/test_ci_template.py`: extend the reviewer-identity test tuples
  with `cursor`; assert both templates carry the cursor jobs / matrix entry
  and `AI_REVIEW_REQUIRE_REAL_CURSOR: "1"`; re-check the template's
  count-based assertions (new GitLab jobs extend existing templates, so
  singleton counts like the `stage: ai_review` and image-line counts must be
  revisited deliberately, not patched blindly).

## Risk / rollback

- **Egress boundary:** the material risk is by design and opt-in — enabling
  cursor sends review inputs to Cursor's backend. Mitigations: disabled by
  default, dedicated credential, `env -i` scrub (no OpenRouter key, no CI
  tokens), documented in three places (config comment, SUPPLY_CHAIN.md,
  README).
- **External-shape guesses** (exact Composer model id, permissions-file
  location/syntax, versioned download URL): all verified at implementation
  time against the pinned binary; each is a one-line change if wrong, and the
  fake-CLI tests don't depend on them.
- **Upstream CLI instability** (reported occasional `-p` hangs): covered by
  the runner's process-group kill; a hung cursor run degrades to a `timeout`
  status and the panel's degradation policy applies.
- **Rollback:** disable via `AI_REVIEW_CURSOR_ENABLED=false` (or revert the
  config entry); the adapter/Dockerfile additions are inert when disabled.
