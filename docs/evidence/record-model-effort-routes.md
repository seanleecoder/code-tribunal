# Evidence record: shipped default-model effort routes / 2026-10-18

Status: passed

Release-runtime-source: 10f2f8deef3ee63c952df76d5f627ed6aa2e7ea9
Release-base-digest: sha256:e6ca87a2082cd4820b780a14164212a33e3d176812541e9ede68dd1b2169ccdd
Release-reviewer-digest: sha256:4279949eadccc6333ce6b07263e7ee5097e25eda49e62e9bc72fdb118614f4a6

> Sanitized final-pair evidence. No credentials, provider sessions, source bodies,
> or sensitive model content are reproduced here.

## Identity

- Platform: GitHub.com / GitHub Actions hosted Linux container jobs.
- Date/time: 2026-10-18T12:43:52Z–2026-10-18T12:47:14Z (UTC).
- Consumer: `seanleecoder/code-tribunal-demo`, Candidate Canary panel
  [PR #42](https://github.com/seanleecoder/code-tribunal-demo/pull/42).
- Consumer workflow run: [39128506701](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/39128506701),
  attempt 1, conclusion `success`; artifact run `gh-39128506701-1`.
- Consumer head: `3cff9e18533267cdfe3bee597491cd2a33d6a44d`.
- Candidate Canary run: [39124481046](https://github.com/seanleecoder/code-tribunal/actions/runs/39124481046);
  see the [canary record](record-candidate-canary.md).
- Runtime source `R`: `10f2f8deef3ee63c952df76d5f627ed6aa2e7ea9`.
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-10f2f8deef3ee63c952df76d5f627ed6aa2e7ea9@sha256:e6ca87a2082cd4820b780a14164212a33e3d176812541e9ede68dd1b2169ccdd`.
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-10f2f8deef3ee63c952df76d5f627ed6aa2e7ea9@sha256:4279949eadccc6333ce6b07263e7ee5097e25eda49e62e9bc72fdb118614f4a6`.
- Effective configuration digest: `d85ca9cdf4cb468dfff80edd4b472305dc9a1824b044daae54601b7efae8cf39`.

## Scope and result

The GitHub panel of the four-seat Candidate Canary supplies the route evidence; no
separate three-seat smoke ran. Its Claude, Codex, and OpenCode model variables
equal the shipped defaults, and the canary clears every effort variable, so each
seat's effort comes from the candidate image's packaged configuration.

| Seat | Resolved model | Resolved effort | Invocation owned by the shipped adapter | Live review / critique |
|---|---|---|---|---|
| Claude | `anthropic/claude-haiku-5.5` | `low` | `--effort low` | success / success |
| Codex | `openai/gpt-6-luna` | `low` | `model_reasoning_effort="low"` | success / success |
| OpenCode | `deepseek/deepseek-v4.1-flash-1017` | `low` | `reasoningEffort: "low"` | success / success |

The prepare manifest's `effective_config` carries these models and efforts, and
the manifest and all eight review and critique status files share the digest
above. The fourth seat, Cursor, resolved to `composer-2.5` with no effort and also
succeeded in both stages; it is outside the default roster and this record.

The invocation column is established by the shipped adapter paths and their
regression tests (`test_openrouter_adapters.py`, `test_config_env_overrides.py`),
together with successful real CLI execution under the bound configuration. No
provider-internal reasoning level, native OpenCode metadata, or billed token usage
was captured; this record does not claim an independent provider echo of any
seat's effort.

## Audit and limitations

`scripts/scan_evidence_leaks.py` over the downloaded inputs, review, and critique
artifacts: 27 files, approximately 0.2 MB, ten pattern/entropy detectors, no
credential material; no exact-value scan. No credential values were retrieved or
recorded. The run used the four-seat canary roster, so it does not certify a real
run of the shipped three-seat default roster. Non-default effort routes, including
the 2.0.2 Claude `medium` and absent-effort routes, remain outside this pass.

## Verdict

Scoped source-/digest-bound pass for the shipped Claude, Codex, and OpenCode `low`
effort routes through successful real review and critique. No non-default effort
route is promoted as newly validated.
