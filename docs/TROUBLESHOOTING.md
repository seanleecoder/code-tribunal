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
| `/ai-review` command ignored | Wrong thread, syntax, or authorization | Root finding marker and author permission | Reply in the finding thread with one exact command line |
| GitHub thread stays open | Built-in token cannot mutate review threads or root comment was deleted | Post warnings and GraphQL error | Add a fine-grained resolve token; preserve root comments |
| GitHub prepare reports stale input | PR changed or checkout is dirty/untracked | Prepare error with selected/current SHA | Rerun current revision; never weaken SHA checks |
| GitHub prepare reports HTTP 406/too-large | GitHub refused a complete raw diff | Prepare log | Split/reduce the PR; do not use incomplete `/files` data as a substitute |
| GitLab child trust audit fails | Include project/SHA, forwarding, inheritance, or bridge contract changed | Auditor errors | Restore the exact hardened example; do not bypass the validator |
| Runtime override appears ignored | Pinned image predates it or variable scope differs | Template image source SHA and manifest effective config | Rotate all image pins together or move override to shared project/repository scope |
| Snapshot rejects repository | Symlink, special file, excessive depth, or unsupported no-follow platform | `BundleError` relative path | Remove/replace the unsupported entry; do not enable link following |

## Reviewer status meanings

- `model_error`: provider/CLI call failed, credential is absent, or endpoint/model
  validation rejected input.
- `schema_error`: model output could not satisfy the structured contract.
- `timeout`: the complete reviewer process group exceeded
  `reviewers.<name>.timeout_seconds`.
- `config_error`: configuration or an environment override failed strict
  validation.

One failed seat normally degrades the panel. Zero usable seats or evidence
integrity failure stops consensus.

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

See [operations](operations.md#failure-behavior) for the full failure matrix and
[artifacts](reference/artifacts-and-schemas.md) for paths and schemas.
