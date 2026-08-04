# SPEC-49 — Suppress OpenCode session-title inference

- **Status:** Superseded by the durable structured-output OpenCode client (source
  implemented; immutable image publication and live-provider acceptance remain
  separate rollout work).
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

The Code Tribunal OpenCode adapter must create each server session with exactly
the fixed title `code-tribunal-ai-review`. The title is fixed, non-empty, and
contains no prompt, repository, pull/merge-request, or user data. It is internal
session metadata only. It must not be derived from the review prompt, input
bundle, filesystem, environment, or configured model.

No `small_model`, title-model variable, reviewer-config key, model override, or
provider change is permitted for this purpose. The existing OpenCode model setting
(`reviewers.opencode.model`, including its supported model override) remains the sole
model-selection interface.

## Implementation boundary

**In scope**

- Start `opencode --pure serve` on loopback, create the titled session through
  the server API, and use the same client for review and critique.
- Send the stage-specific `json_schema` format so OpenCode's structured-output
  tool is the primary transport; retain strict complete-JSON text recovery as
  compatibility handling only.
- Document the deterministic, data-free internal title behavior in
  `docs/configuration.md` without adding a control.
- Add an image-build guard in `ai-review/images/reviewer.Dockerfile` that requires
  the pinned server CLI to expose both loopback listen flags.
- Add API-contract, fixture, malformed-output, and fake-adapter coverage.

**Explicitly out of scope**

- The unrelated consensus failure.
- Model-default changes, model/provider overrides, or a `small_model`/title-model
  setting. The OpenCode package is upgraded only to the exact reviewed pin used
  by the durable client.
- Image publication, template repinning, immutable digest updates, release-input
  changes, or release-history changes.
- Any claim that a live provider, OpenRouter dashboard, or currently pinned
  production image has changed behavior.

## Acceptance criteria

- API-contract tests prove that both review and critique create exactly one
  session with title `code-tribunal-ai-review` and send the matching stage schema.
- The configured `--model`, `--agent`, permissions, tool restrictions, and isolated
  environment are unchanged.
- A model override still changes only the configured primary model. It cannot alter
  the static title or introduce another model setting.
- The reviewer-image build fails if the pinned OpenCode CLI no longer supports
  the loopback `serve --hostname` and `serve --port` flags.
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
