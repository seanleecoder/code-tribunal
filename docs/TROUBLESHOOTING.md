# Troubleshooting

Use the symptom, collect the named evidence, then take the action. Do not paste
credentials or sensitive model content into issues.

| Symptom | Likely cause | Evidence | Action |
|---|---|---|---|
| Reviewer job failed | Provider/CLI/model/config/timeout error | `out/status/<reviewer>.json` and redacted job log | Fix credential scope/model/config or retry transient failure |
| Reviewer succeeded but does not count | All findings dropped or batch not resolution-eligible | Finding batch quality counts and `usable_for_resolution` | Use a stronger/compatible model; do not lower safety thresholds blindly |
| Consensus exits 3 | No usable panel, config drift, wrong run, malformed or spoofed artifact | Consensus log, manifest run/config digests, finding/critique identities | Rerun from prepare with identical project-scoped variables; do not mix artifacts |
| Gate exits 7 with no blocker | Post/state failure has precedence | `out/post/post_result.json`, `out/gate/gate_result.json` | Repair platform API/state capacity and rerun post/gate |
| Pipeline is green but no comments appear | Zero surfaced findings, stale head, or advisory-only panel | Consensus summary and post result | Confirm expected policy; let newer revision run after stale head |
| Gate does not block a merge | Gate disabled, no blocker quorum, advisory panel, or platform setting missing | Config, consensus, gate artifact, required checks/settings | Enable gate and platform enforcement only after validation |
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
| Reviewer appears slow or stuck | Provider retry, repository exploration, or stalled CLI | Stage-specific status artifact, `duration_ms`, timeout status, optional streamed adapter log | Check review against `reviewers.<name>.timeout_seconds` and critique against `reviewers.<name>.critique_timeout_seconds` (or its legacy fallback); temporarily set `AI_REVIEW_STREAM_ADAPTER_LOGS=1`, then unset it |
| GitHub: every seat drops its findings and consensus exits 3 | 1.0.0-only defect: the PR adds or deletes a file, so anchor resolution rejects `/dev/null` | Reviewer log line `absolute paths are not allowed: /dev/null`; `dropped_finding_count` equals `raw_finding_count` | Fixed in 1.0.1 — upgrade the pinned images. On 1.0.0 images, land file additions in a separate change request or re-run after they merge |
| Local provider call rejects an endpoint | Developer shell exported a non-canonical provider URL | Redacted `model_error` naming `ANTHROPIC_BASE_URL` or `OPENROUTER_BASE_URL` | Run with `env -u ANTHROPIC_BASE_URL -u OPENROUTER_BASE_URL make review-local ...` |

## Reviewer status meanings

- `model_error`: provider/CLI call failed, credential is absent, or endpoint/model
  validation rejected input.
- `schema_error`: model output could not satisfy the structured contract.
- `timeout`: the complete process group exceeded the stage-specific timeout:
  `reviewers.<name>.timeout_seconds` for review or
  `reviewers.<name>.critique_timeout_seconds` for critique. Legacy configs that
  omit the latter use `timeout_seconds` for both stages.
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
always captured for parsing. A parse or validation failure also writes a
redacted, bounded head/tail preview to
`out/status/<stage>-<reviewer>-parse-debug.txt`, so post-mortem evidence remains
available without enabling live streaming.

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
3. The `consensus` job exits 3, so `post` and `gate` never run and the required
   `gate` check cannot succeed.

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
merged. Disabling `merge_gate.enabled` unblocks the merge but does not recover the
dropped findings.

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

Provider endpoint pinning also applies locally. If the developer shell exports
an Anthropic or OpenRouter endpoint for unrelated tooling, remove it for the
harness:

```bash
env -u ANTHROPIC_BASE_URL -u OPENROUTER_BASE_URL \
  make review-local REVIEWER=claude
```

See [operations](operations.md#failure-behavior) for the full failure matrix and
[artifacts](reference/artifacts-and-schemas.md) for paths and schemas.
