# Troubleshooting

Use the symptom, collect the named evidence, then take the action. Do not paste
credentials or sensitive model content into issues.

| Symptom | Likely cause | Evidence | Action |
|---|---|---|---|
| Reviewer job failed | Provider/CLI/model/config/timeout error | `out/status/<reviewer>.json` and redacted job log | Fix credential scope/model/config or retry transient failure |
| Reviewer succeeded but does not count | All findings dropped or batch not resolution-eligible | Finding batch quality counts and `usable_for_resolution` | Use a stronger/compatible model; do not lower safety thresholds blindly |
| Consensus exits 3 | No usable panel, corrupt manifest config, wrong run, malformed or spoofed artifact | Consensus log: a failed panel prints the review health with each failed seat; otherwise the integrity error. Manifest run/config digests, finding/critique identities | Rerun from prepare with the intended prepare-time variables; do not mix artifacts |
| `post` refuses a consensus artifact with no `run_health` | The consensus artifact came from an earlier release than the post image | `post` job log | Re-run from prepare with both images from one release |
| `post` exits nonzero | Publication or state persistence failed (`failed`, `partial_failed`, `state_overflow`) — findings never affect the exit status | `out/post/post_result.json` `status` and `warnings` | Repair platform API/state capacity and rerun post |
| Pipeline is green but no comments appear | Stale head: a newer revision superseded the run, so post made no mutation | `post_result.json` `status: stale_head` | Let the newer revision's run publish |
| Summary says `re-run recommended` or `degraded` | A seat or critic failed, or findings were dropped or capped | The summary's problem list and run details; `out/status/` and the consensus job log | Follow the action line; re-run from prepare, never individual jobs |
| Pipeline is green, a summary comment exists, but no threads were posted | No finding had two independent supporters: either nothing was found, or every finding is FYI. This is expected, not a fault | The summary's review health headline and FYI list; consensus `summary.surface_count` is 0 | Investigate only if the health grade is not `full`: a lost seat lowers what can reach two supporters |
| A finding you expected is only FYI | Only one reviewer identity supported it; severity does not promote it | Group `support_count`, `contributing_reviewers`, `agreeing_critics` | Expected: two independent identities are required to surface. Check for a failed or silently empty seat |
| Duplicate-looking discussion | Lost/untrusted state, changed bot identity, conservative rematch, or different category | Issue markers, state author, `issue_id`, record status | Restore bot/state ownership; distinguish a true duplicate from a new identity |
| `/ai-review` command ignored | Wrong thread, syntax, insufficient access, or collaborator permission could not be verified | Root finding marker, author permission, and `post_result.json` warnings naming the note and author | Reply in the finding thread with one exact command line ([syntax and access](reference/revision-lifecycle.md#human-dispositions)); on GitHub organization repositories configure the fine-grained resolve token |
| Thread resolution or reopening fails | Platform token cannot mutate the thread or its root comment was deleted; `post` reports `partial_failed` and exits nonzero | `post_result.json` warnings and platform API error | Repair the token or thread access and rerun; on GitHub add a fine-grained resolve token and preserve root comments |
| GitHub prepare reports stale input | PR changed or checkout is dirty/untracked | Prepare error with selected/current SHA | Rerun current revision; never weaken SHA checks |
| GitHub prepare reports dubious repository ownership | Custom image user/config, or prepare running from an unexpected checkout | Image source SHA, working directory, checkout owner, container uid, and prepare log | Publish the fix from trusted `main`, update all image pins together, and verify custom workflow paths; never configure `safe.directory=*` |
| GitHub prepare reports HTTP 406/too-large | GitHub refused a complete raw diff | Prepare log | Split/reduce the PR; do not use incomplete `/files` data as a substitute |
| GitLab prepare reports a truncated or collapsed diff | Both the paginated and raw-diff APIs failed to provide one complete matching change | Prepare log naming the affected path and fallback failure | Split/reduce the MR or repair the GitLab diff backend; do not ignore the file or bypass completeness checks |
| GitLab prepare reports that the MR version changed during diff collection | The source or target revision moved while prepare read the paginated or raw diff | Prepare log and current MR revision | Rerun the pipeline for the current revision; do not reuse artifacts from the stale run |
| GitLab trust audit fails | Include project/SHA/path changed, or a reserved Code Tribunal job is redefined | Auditor errors | Restore the exact direct include example; do not bypass the validator |
| Runtime override appears ignored | Pinned image predates it or prepare did not receive it | Template image source SHA and manifest effective config | Rotate all image pins together or make the override visible to prepare and start a new run |
| Snapshot rejects repository | Symlink, special file, excessive depth, or unsupported no-follow platform | `BundleError` relative path | Remove/replace the unsupported entry; do not enable link following |
| Reviewer appears slow or stuck | Provider retry, repository exploration, or stalled CLI | Stage-specific status artifact, `duration_ms`, timeout status and redacted timeout artifacts | Check review against `reviewers.<name>.timeout_seconds` and critique against `reviewers.<name>.critique_timeout_seconds` (900 seconds when it is omitted, regardless of `timeout_seconds`); inspect the timeout diagnostics before increasing the budget |
| Reviewer reports `model_error` naming a model id | A `AI_REVIEW_<REVIEWER>_MODEL` override contains characters that cannot be passed safely to a CLI or interpolated into adapter config | Redacted `model_error` reading `model id has unsupported characters` | Set the override to a plain provider/slug id, optionally with an OpenRouter `:variant` suffix |

## Reviewer status meanings

- `model_error`: provider/CLI call failed, credential is absent, or endpoint/model
  validation rejected input.
- `schema_error`: model output could not satisfy the structured contract.
- `timeout`: the complete process group exceeded the stage-specific timeout:
  `reviewers.<name>.timeout_seconds` for review or
  `reviewers.<name>.critique_timeout_seconds` for critique.
- `config_error`: configuration or an environment override failed strict
  validation.

One failed seat normally degrades the panel. Zero usable seats or evidence
integrity failure stops consensus.

Review and critique timeout evidence is separate: compare each stage's
`duration_ms` and timeout status before changing configuration. Do not infer a
critique timeout from a slow review seat, and do not raise the critique budget
until production artifacts show actual critique timeouts (especially for large
finding pools).

## Adapter diagnostics

Adapter stdout and stderr are captured in private temporary files, with a
32 MiB budget per stream checked every 100 ms. Exceeding it fails the seat with
`model_error` and `AdapterOutputLimitError`; the job log retains bounded, redacted
stdout and stderr tails to diagnose the failure. Partial output is never treated
as a clean review. After completion, the job log shows at most the last 4,000 redacted
stderr characters, with a truncation notice when necessary. Capture files are
closed and removed after the adapter's process group is terminated, including
any children remaining after a normal adapter exit.
Incomplete private-key blocks are redacted through the end of the captured output.

A parse or validation failure writes two redacted artifacts:

- `out/status/<stage>-<reviewer>-parse-debug.txt` — bounded head/tail preview of
  stdout and stderr, useful for a first look.
- `out/status/<stage>-<reviewer>-parse-raw-stdout.txt` — the complete adapter
  stdout, bounded at 2 MiB. Read this one when the preview's elided middle is
  where the answer should have been; its newline structure is intact, so a
  stream can be replayed or reused as a test fixture directly.

Timeouts write the same pair with `timeout` instead of `parse` in the names,
preserving output emitted before the process group was killed. Temporary capture
files are never uploaded as artifacts.

A `schema_error` whose message names the *model* rather than the output — for
example `model emitted no answer part` — means the reviewer never produced an
answer, only reasoning or tool calls. That is a model or prompt problem, not
malformed adapter output.

## Configuration drift

Prepare binds the effective configuration by SHA-256, and every later stage
fails when it sees drift; see
[stage visibility and integrity](configuration.md#stage-visibility-and-integrity).
Set supported
overrides at project/group or repository workflow scope and rerun from prepare.

## Local reproduction

```bash
make review-local REVIEWER=claude LOCAL_OUT=/tmp/code-tribunal-review
make consensus-local LOCAL_OUT=/tmp/code-tribunal-consensus
make validate-local LOCAL_OUT=/tmp/code-tribunal-validation
```

These commands use deterministic mock output. A real local provider call sends
repository content to that provider and should be run only under the operator's
data-handling policy.

Provider endpoint pinning needs nothing from the caller: the adapter runner
supplies each seat's pinned endpoint itself, so an `ANTHROPIC_BASE_URL` or
`OPENROUTER_BASE_URL` the developer shell exports for unrelated tooling is
overridden for the harness and does not have to be unset.

See [operations](operations.md#failure-behavior) for the full failure matrix and
[artifacts](reference/artifacts-and-schemas.md) for paths and schemas.
