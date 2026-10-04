# Evidence record: shipped default-model effort routes / 2026-10-04

Status: passed

Release-runtime-source: 3db908f06d141d23de99e7578ae5b5482ebaa498
Release-base-digest: sha256:2f3ffaba159e067825d72e889872ac6479bb31461943b6d86516a266e1faf3e9
Release-reviewer-digest: sha256:7c59447733e5b6640f7ff759ab879c4682310054de130515841e91389a9b2447

> Sanitized final-pair evidence. No credentials, provider sessions, source bodies,
> or sensitive model content are reproduced here.

## Identity

- Platform: GitHub.com / GitHub Actions hosted Linux container jobs.
- Date/time: 2026-10-04T20:59:10Z–2026-10-04T21:03:07Z (UTC).
- Consumer: `seanleecoder/code-tribunal-demo`, temporary
  [PR #39](https://github.com/seanleecoder/code-tribunal-demo/pull/39).
- Workflow run: [37234212093](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37234212093),
  attempt 1, conclusion `success`; artifact run `gh-37234212093-1`.
- Consumer head and temporary workflow commit: `17d8ad8ec5ae60d54846d5839b90376e9224653f`.
- Runtime source `R`: `3db908f06d141d23de99e7578ae5b5482ebaa498`.
- Protected-main image publication: [37233827865](https://github.com/seanleecoder/code-tribunal/actions/runs/37233827865).
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-3db908f06d141d23de99e7578ae5b5482ebaa498@sha256:2f3ffaba159e067825d72e889872ac6479bb31461943b6d86516a266e1faf3e9`.
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-3db908f06d141d23de99e7578ae5b5482ebaa498@sha256:7c59447733e5b6640f7ff759ab879c4682310054de130515841e91389a9b2447`.
- Resolved configuration digest: `854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`.
- Canonical workflow copied from `R`, with the final pair, blank model/effort and
  roster overrides, explicit real-provider guards, a trusted smoke-only tool rule,
  and sanitized native OpenCode metadata capture. Packaged YAML was not replaced.

## Scope and result

The same passing final-pair three-seat run supplies route evidence; no additional
paid campaign was needed. See the [default-roster record](record-github-default-model-smoke.md)
for job IDs, artifact counts, tool metadata, posting, and cleanup.

| Seat | Resolved model | Resolved effort | Invocation owned by the shipped adapter | Live review / critique |
|---|---|---|---|---|
| Claude | `anthropic/claude-haiku-4.5` | `medium` | `--effort medium` | success / success |
| Codex | `openai/gpt-6-luna` | `low` | `model_reasoning_effort="low"` | success / success |
| OpenCode | `xiaomi/mimo-v2.6-flash` | absent YAML key | `reasoningEffort` omitted | success / success |

Preparation and every successful review/critique artifact share configuration
digest `854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`. The downloaded config keeps
Claude `medium`, Codex `low`, and no OpenCode `effort` key. Model and effort
environment overrides were blank, with the OpenCode effort override omitted.
Native OpenCode provider metadata agrees on the Flash model for both stages.

The invocation column is established by the unchanged shipped adapter paths and
socket-capable regression tests (`test_openrouter_adapters.py`,
`test_config_env_overrides.py`), together with successful real CLI execution under
the bound configuration. No provider-internal reasoning level or billed token
usage was captured; this record does not claim an independent provider echo of
Claude/Codex effort. Explicit `unset` clearing is regression-covered, not a
separate live campaign.

## Audit and limitations

The default-roster artifact scan passed: 35 files, approximately 0.1 MB, ten
pattern/entropy detectors; no exact-value scan. No credential values were retrieved
or recorded. Non-default `max`/`xhigh` acceptance, MiMo Pro, Grok, provider-internal
reasoning, and billed cost remain outside this pass. Historical route attestations
remain in git history and are not substituted for final-pair evidence.

## Verdict

Scoped source-/digest-bound pass for the shipped Claude `medium`, Codex `low`, and
absent MiMo effort routes through successful real review and critique. No
non-default effort route is promoted as newly validated.
