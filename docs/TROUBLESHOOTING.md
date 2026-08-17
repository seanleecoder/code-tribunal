# Troubleshooting

Use the symptom, collect the named evidence, then take the action. Do not paste
credentials or sensitive model content into issues.

| Symptom | Likely cause | Evidence | Action |
|---|---|---|---|
| Reviewer job failed | Provider/CLI/model/config/timeout error | `out/status/<reviewer>.json` and redacted job log | Fix credential scope/model/config or retry transient failure |
| Reviewer succeeded but does not count | All findings dropped or batch not resolution-eligible | Finding batch quality counts and `usable_for_resolution` | Use a stronger/compatible model; do not lower safety thresholds blindly |
| Consensus exits 3 | No usable panel, config drift, wrong run, malformed or spoofed artifact | Consensus log, manifest run/config digests, finding/critique identities | Rerun from prepare with identical project-scoped variables; do not mix artifacts |
| `post` exits nonzero with no blocker | Publication or state persistence failed (`failed`, `partial_failed`, `state_overflow`) — findings never affect the exit status | `out/post/post_result.json` `status` and `warnings` | Repair platform API/state capacity and rerun post |
| Pipeline is green but no comments appear | Zero surfaced findings, stale head, or a degraded panel where nothing reached two independent supporters | Consensus summary, group `support_count`, and post result | Confirm expected policy; let newer revision run after stale head |
| Pipeline is green, a summary comment exists, but no threads were posted | Every finding had exactly one independent supporter, so all of them are FYI. This is the expected outcome for an unconfirmed finding, not a fault | Consensus `summary.surface_count` is 0 with a nonzero `fyi_count`; each group's `support_count` is 1 | Read the FYI list in the summary comment. Investigate only if a seat failed or returned nothing — a lost seat lowers what can reach two supporters |
| A finding you expected is only FYI | Only one reviewer identity supported it; severity does not promote it | Group `support_count`, `contributing_reviewers`, `agreeing_critics` | Expected: two independent identities are required to surface. Check for a failed or silently empty seat |
| Duplicate-looking discussion | Lost/untrusted state, changed bot identity, conservative rematch, or different category | Issue markers, state author, `issue_id`, remap status | Restore bot/state ownership; distinguish a true duplicate from a new identity |
| `/ai-review` command ignored | Wrong thread, syntax, insufficient access, or collaborator permission could not be verified | Root finding marker, author permission, and `post_result.json` warnings naming the note and author | Reply in the finding thread with one exact command line; on GitHub organization repositories configure the fine-grained resolve token |
| GitHub thread stays open | Built-in token cannot mutate review threads or root comment was deleted | Post warnings and GraphQL error | Add a fine-grained resolve token; preserve root comments |
| GitHub prepare reports stale input | PR changed or checkout is dirty/untracked | Prepare error with selected/current SHA | Rerun current revision; never weaken SHA checks |
| GitHub prepare reports dubious repository ownership | Old image pin, custom image user/config, or prepare running from an unexpected checkout | Image source SHA, working directory, checkout owner, container uid, and prepare log | Publish the fix from trusted `main`, update all image pins together, and verify custom workflow paths; never configure `safe.directory=*` |
| GitHub prepare reports HTTP 406/too-large | GitHub refused a complete raw diff | Prepare log | Split/reduce the PR; do not use incomplete `/files` data as a substitute |
| GitLab prepare reports a truncated or collapsed diff | Both the paginated and raw-diff APIs failed to provide one complete matching change | Prepare log naming the affected path and fallback failure | Split/reduce the MR or repair the GitLab diff backend; do not ignore the file or bypass completeness checks |
| GitLab prepare reports that the MR version changed during diff collection | The source or target revision moved while prepare read the paginated or raw diff | Prepare log and current MR revision | Rerun the pipeline for the current revision; do not reuse artifacts from the stale run |
| GitLab child trust audit fails | Include project/SHA, forwarding, inheritance, or bridge contract changed | Auditor errors | Restore the exact hardened example; do not bypass the validator |
| Runtime override appears ignored | Pinned image predates it or variable scope differs | Template image source SHA and manifest effective config | Rotate all image pins together or move override to shared project/repository scope |
| Snapshot rejects repository | Symlink, special file, excessive depth, or unsupported no-follow platform | `BundleError` relative path | Remove/replace the unsupported entry; do not enable link following |
| Reviewer appears slow or stuck | Provider retry, repository exploration, or stalled CLI | Stage-specific status artifact, `duration_ms`, timeout status, optional streamed adapter log | Check review against `reviewers.<name>.timeout_seconds` and critique against `reviewers.<name>.critique_timeout_seconds` (or `min(timeout_seconds, 900)` for its legacy fallback); temporarily set `AI_REVIEW_STREAM_ADAPTER_LOGS=1`, then unset it |
| GitHub: every seat drops its findings and consensus exits 3 | 1.0.0-only defect: the PR adds or deletes a file, so anchor resolution rejects `/dev/null` | Reviewer log line `absolute paths are not allowed: /dev/null`; `dropped_finding_count` equals `raw_finding_count` | Fixed in 1.0.1 — upgrade the pinned images. On 1.0.0 images, land file additions in a separate change request or re-run after they merge |
| Reviewer reports `model_error` naming a model id | A `AI_REVIEW_<REVIEWER>_MODEL` override contains characters that cannot be passed safely to a CLI or interpolated into adapter config | Redacted `model_error` reading `model id has unsupported characters` | Set the override to a plain provider/slug id, optionally with an OpenRouter `:variant` suffix |

## Reviewer status meanings

- `model_error`: provider/CLI call failed, credential is absent, or endpoint/model
  validation rejected input.
- `schema_error`: model output could not satisfy the structured contract.
- `timeout`: the complete process group exceeded the stage-specific timeout:
  `reviewers.<name>.timeout_seconds` for review or
  `reviewers.<name>.critique_timeout_seconds` for critique. Legacy configs that
  omit the latter resolve critique to `min(timeout_seconds, 900)`.
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

`AI_REVIEW_STREAM_ADAPTER_LOGS=1` mirrors reviewer stdout and stderr while the
process runs. It is off by default because verbose Claude stream output can
exceed GitLab's job-log limit and truncate the useful tail. Adapter output is
always captured for parsing. A parse or validation failure also writes two
redacted artifacts, so post-mortem evidence remains available without enabling
live streaming:

- `out/status/<stage>-<reviewer>-parse-debug.txt` — bounded head/tail preview of
  stdout and stderr, useful for a first look.
- `out/status/<stage>-<reviewer>-parse-raw-stdout.txt` — the complete adapter
  stdout, bounded at 2 MiB. Read this one when the preview's elided middle is
  where the answer should have been; its newline structure is intact, so a
  stream can be replayed or reused as a test fixture directly.

A `schema_error` whose message names the *model* rather than the output — for
example `model emitted no answer part` — means the reviewer never produced an
answer, only reasoning or tool calls. That is a model or prompt problem, not
malformed adapter output.

## Human commands

Reply on the finding's GitLab discussion or GitHub root inline review comment.
The command must appear alone on a line:

```text
/ai-review resolve
/ai-review wontfix
/ai-review reopen
```

GitLab requires Developer access or higher. GitHub accepts Write, Maintain, or
Admin. UI-only thread resolution is not the same as durable `wontfix`.

## GitHub: every finding dropped and consensus exits 3 (added or deleted files)

Defect **present in the shipped 1.0.0 runtime and fixed in 1.0.1**. If your
templates still pin the 1.0.0 image digests, the workaround below is required;
repinning to the 1.0.1 pair removes the need for it. Symptoms, in the order you
meet them:

1. A reviewer job log contains `absolute paths are not allowed: /dev/null` followed
   by `kept 0 finding(s), dropped N malformed/unresolvable finding(s)`.
2. `out/status/<reviewer>.json` shows `dropped_finding_count` equal to
   `raw_finding_count` with `usable_for_resolution: false` — often on every seat.
3. The `consensus` job exits 3, so no review is published. On GitHub the `post`
   job then fails rather than being skipped, reporting the upstream consensus
   failure.

Cause: GitHub renders an added file's diff with `--- /dev/null`, which anchor
resolution rejects as an absolute path while scanning for the anchor's file. It
triggers when a finding is on an added or deleted file, or on a file ordered after
one in the diff. It affects real reviewers, not only the deterministic mock. GitLab
is unaffected, because its prepared diff uses `--- a/<path>` for added files.

Fix: repin both image digests to the 1.0.1 pair. Anchor finalization now maps the
added/deleted-file sides instead of failing the scan, so a finding on an added file
is accepted (`accepted_finding_count == raw_finding_count`).

Workaround on 1.0.0 images: split file additions into a separate change request
from the code you want reviewed, or re-run the review once the added file has
merged. Nothing in Code Tribunal blocks the merge in the first place — the review
is simply missing its findings.

## Configuration drift

Prepare records the consequential effective configuration in the manifest and
binds it by SHA-256. Current consensus behavior **fails with exit 3** when a
later stage sees consequential drift; it does not merely warn. Set supported
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
