# SPEC-49 — Suppress OpenCode session-title inference

- **Status:** Superseded by [SPEC-50](spec-50-opencode-structured-reviewer-output.md).
  The fixed, data-free session title below remains in force; SPEC-50 replaces only the
  `opencode --pure run --title` mechanism, because the session is now created through
  the server API. Everything recorded here is preserved unedited as the forensic record
  of the title-inference incident.
- **Classification:** S / operational-policy correctness.
- **Depends on:** the existing OpenCode adapter contract in
  [SPEC-19](../history/specs/spec-19-opencode-reviewer-optimization.md).
- **Owner split:** the coding agent owns the source, unit tests, documentation, and
  image-build guard below. Image publication, immutable digest repinning, and a real
  no-extra-model smoke require a separately approved rollout.

## Incident and forensic rationale

The primary OpenCode review request used the configured model. Separately, pinned
`opencode-ai` 1.17.18 automatically generated a session title with the exact
`google/gemini-3.5-flash` model. That extra request was independent of the configured
review model and was not an intended second reviewer/model route.

The pinned upstream [title-generation path](https://github.com/anomalyco/opencode/blob/v1.17.18/packages/opencode/src/session/prompt.ts#L178-L234)
shows that a default session title causes OpenCode to obtain a small model and issue a
second request. The matching [run-command path](https://github.com/anomalyco/opencode/blob/v1.17.18/packages/opencode/src/cli/cmd/run.ts#L3195-L3244)
passes a non-empty `--title` value into fresh-session creation. The incident's exact
route is recorded here as forensic rationale, not as a new configurable model
selection.

## Non-negotiable decision

Every executable `opencode --pure run` invocation in the Code Tribunal OpenCode
adapter must include exactly:

~~~text
--title "code-tribunal-ai-review"
~~~

The title is fixed, non-empty, and contains no prompt, repository, pull/merge-request,
or user data. It is internal session metadata only. It must not be derived from the
review prompt, input bundle, filesystem, environment, or configured model.

No `small_model`, title-model variable, reviewer-config key, model override, or
provider change is permitted for this purpose. The existing OpenCode model setting
(`reviewers.opencode.model`, including its supported model override) remains the sole
model-selection interface.

## Implementation boundary

**In scope**

- Add the static `--title "code-tribunal-ai-review"` argument and an explanatory
  regression-prevention comment to `ai-review/adapters/opencode.sh`. The shared
  invocation serves both review and critique.
- Document the deterministic, data-free internal title behavior in
  `docs/configuration.md` without adding a control.
- Add an image-build guard in `ai-review/images/reviewer.Dockerfile` that requires
  `opencode --pure run --help` to expose `--title`; a future pinned CLI that drops
  or renames the flag must fail the build.
- Add fake-CLI adapter coverage for both stages and a source-contract test for the
  Dockerfile guard.

**Explicitly out of scope**

- The unrelated consensus failure.
- Model-default changes, OpenCode package upgrades, model/provider overrides, or
  a `small_model`/title-model setting.
- Image publication, template repinning, immutable digest updates, release-input
  changes, or release-history changes.
- Any claim that a live provider, OpenRouter dashboard, or currently pinned
  production image has changed behavior.

## Acceptance criteria

- Fake-CLI tests prove that both review and critique receive exactly one static
  `--title` argument with value `code-tribunal-ai-review`.
- The configured `--model`, `--agent`, permissions, tool restrictions, and isolated
  environment are unchanged.
- A model override still changes only the configured primary model. It cannot alter
  the static title or introduce another model setting.
- The reviewer-image build fails if the pinned OpenCode CLI no longer supports
  `--title`.
- Targeted adapter tests, documentation checks, full quality, and the pull-request
  image build/preflight pass without credentials or a real OpenRouter request.
- Real OpenRouter verification is deferred. Source-only work does not modify the
  currently pinned production image or prove dashboard behavior until a later image
  publication and template-repin rollout.

## Handoff and rollout boundary

The coding agent may implement and verify only the source-level contract above. The
separate rollout owner must approve and perform image publication, record immutable
image digests, repin consumers/templates, and run a credentialed smoke that proves
the configured primary model is the only OpenRouter model request. That smoke is
required before making a live-provider or production-image claim; it is not implied
by the local fake-CLI, Docker-help, or image-preflight checks.
