# Evidence record: OpenCode max-effort route / 2026-08-10

Status: waived

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c
Release-evidence-waived: Model and effort defaults in config/review.yaml are unchanged since v1.0.2 (only comments changed) and no effort profile changed; the Candidate Canary exercised shipped default effort on all four seats (record-candidate-canary.md), so the non-default max/xhigh route checks were not rerun.

> **2.0.0 disposition: waived, not re-run.** The binding above is the 2.0.0
> candidate; the live observations below are historical and bound to the
> coordinates they name. `scripts/check_release_inputs.py` matches the waiver
> reason against `verification.evidence_waivers` verbatim.

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

## Scope

This row covers the non-default OpenCode `max` effort route added in 1.0.2. It does
not cover the default-effort route, which has separate source-bound public evidence
in [the GitHub default-model smoke](record-github-default-model-smoke.md), or make
any claim about Codex effort settings.

## Evidence basis

- The operator confirmed that OpenCode `max` effort was already exercised and
  accepted in real runs on a real project. Those coordinates and artifacts are not
  reproduced in this public repository, so the statement is recorded as operator
  attestation, not promoted to a source/image-bound pass.
- The checked adapter forwards `max` unchanged as `reasoningEffort`; it neither
  rejects nor remaps the value. Focused tests cover the exact forwarding path.
- Runtime source `54dffa1` differs from the previously frozen candidate only in the
  post-provider OpenCode client handling of exact stringified structured items. It
  does not alter model selection, effort selection, request construction, or the
  provider route.
- The final public campaign used default effort and therefore is not evidence for
  `max`.

## Residual risk

There is no public, independently inspectable 1.0.2 run that binds an observed
provider acceptance of `max` to the exact source and image pair above. A future
provider or CLI behavior change could therefore affect this route without being
detected by the scoped public campaign. Provider rejection still fails closed as an
adapter failure; the runtime never silently substitutes a lower effort.

## Verdict

Explicit current-release waiver, accepted by the operator. Historical real-project
validation supports the decision, but the route is not advertised here as a new
source-bound public pass.
