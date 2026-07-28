# Evidence record: Cursor reviewer real runs / 2026-07-24

Status: historical supporting (non-release; SPEC-21 Cursor-enablement partial)

Release-binding: none (supporting evidence; not cited by release inputs)

This supplemental record captures the supplied real-project and dogfood runs. It
is not cited by `release/release-inputs.json` and does not add Cursor to the 1.0
or 1.0.1 release-gating matrix. It must not be promoted as Cursor-enablement
evidence because both runs used a historical reviewer image and reported
`model: auto`.

## Identity

- GitLab real project: private consumer, internal pipeline `185695` (project,
  host, MR number, and source SHA intentionally omitted from this public record).
- GitLab Cursor review job: internal job `2591633`.
- GitLab Cursor critique job: internal job `2591637`.
- GitLab consensus job: internal job `2591638`.
- GitHub dogfood: [workflow run 30080420563](https://github.com/seanleecoder/code-tribunal/actions/runs/30080420563), source SHA
  `22b965f2f71bccc083e72c2cd2e8ff96c1d5e65f`.
- GitHub Cursor review job: [89440587942](https://github.com/seanleecoder/code-tribunal/actions/runs/30080420563/job/89440587942).
- GitHub Cursor critique job: [89442064730](https://github.com/seanleecoder/code-tribunal/actions/runs/30080420563/job/89442064730).
- Reviewer image used by both runs:
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-15d424feea730a04338ed423bf93b8797d807bbc@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`.
- GitLab effective-config digest: `affeba28eada0072c198c538ce6afacd6f8367b09ba2cc2ec85b51ee6e337d14`.
- GitHub effective-config digest: `cf2d0e8a44b6688de1d6c01150a9ef572e078d1bf99e3d7c199e2b854a995ef1`.

## Topology and auditability

- GitLab used a private consumer with the canonical GitLab template; GitHub used
  the public dogfood repository and canonical workflow. Private template/project
  coordinates are intentionally omitted.
- An internal operator inspected the GitLab review, critique, consensus, post,
  and gate traces plus downloaded artifacts. Both job traces reported the shared
  reviewer image reference and digest recorded below; the public GitHub run is
  independently inspectable, but the GitLab counts and digests are not publicly
  independently verifiable.
- No private source, credentials, proprietary model content, or private GitLab
  URLs are reproduced here. Internal operators can reconcile the sanitized job
  IDs above in the private CI system.

## Preconditions

- Both runs used the Cursor-enabled reviewer job and the real-only Cursor guard
  from the canonical CI contract; no secret value is recorded here.
- The successful Cursor finding batches, rather than skipped artifacts, show that
  the adapter took the real execution path and returned usable output.
- The reviewer image is a historical pinned image, not the final 1.0 image pair
  or the 1.0.1 pair; this record is therefore supporting evidence only.

## Actual result

| Run | Cursor review | Cursor critique | Panel result |
|---|---|---|---|
| GitLab pipeline 185695 | `adapter_status: success`; `model: auto`; 2 raw, 2 accepted, 0 dropped; `usable_for_resolution: true` | `status: success` | `panel_status: full`; successful reviewers `claude`, `codex`, `cursor`; no failed reviewers; gate passed with `no_blocking_consensus` |
| GitHub run 30080420563 | `adapter_status: success`; `model: auto`; 1 raw, 1 accepted, 0 dropped; `usable_for_resolution: true` | `status: success` | `panel_status: full`; successful reviewers `claude`, `codex`, `cursor`; no failed reviewers; gate succeeded with `block_merge: false` |

GitLab review completed in 304.258 seconds and GitHub review completed in
404.890 seconds, both within the configured 900-second reviewer timeout. The
Cursor traces show the pinned image, `run_reviewer.sh cursor review` / `critique`,
and successful findings/critiques artifact uploads.

## Acceptance accounting

Observed and now closed as a supporting live-evidence subclaim:

- the real-key Cursor route can execute in a real consumer and in the dogfood
  repository;
- the adapter emits schema-valid, resolution-eligible finding batches and
  critique artifacts; and
- Cursor can participate in a full panel and complete the downstream consensus,
  post, and gate stages on the observed non-blocking paths. These runs do not
  prove that a Cursor-backed blocking finding makes a required check block.

Still open for SPEC-21 Cursor enablement:

- Both artifacts record only `model: auto`; neither identifies the exact Composer
  model slug. The exact model must still be discovered and pinned before this
  acceptance gate is closed.
- The permission smoke accepts an explicit model argument; the enablement run
  must pass the exact pinned slug rather than the historical `auto` value.
- Cursor still needs a fresh real-key fixture review/critique against the final
  image pair, with the exact model in the artifact and the runtime/image/config
  coordinates recorded.
- The product contract must explicitly accept prompt-bundle-only ask-mode
  reviews, or the adapter must move to an execution mode whose reads work and
  the permission/real-run evidence must be repeated. If Cursor is expected to
  contribute merge-blocking findings, a blocking fixture and genuinely blocking
  required-check result are also required.
- Neither run included the hostile prompt that requests a sentinel write and a
  shell command. The real-image permission-denial gate remains open.

## Audit

- GitLab artifacts inspected: `out/findings/cursor.json`,
  `out/status/cursor.json`, `out/critiques/cursor.json`,
  `out/pooled_findings/cursor.json`, and the consensus/gate artifacts.
- GitHub artifacts inspected: `findings/cursor.json`, `status/cursor.json`,
  `critiques/cursor.json`, `pooled_findings/cursor.json`, and
  `consensus.json`.
- Logs inspected: the complete Cursor review and critique job traces linked
  above. No credential values are reproduced.
- Sensitive model content is omitted; only counts, status fields, digests, and
  bounded run metadata are recorded.
- The permission smoke remains a separate trusted-image check; these ordinary
  review runs cannot substitute for it.

## Verdict

Supporting pass for real Cursor integration at the two recorded historical
source/image coordinates. This closes the live execution/artifact-validity
subclaim but does not close SPEC-21 Cursor enablement, establish the exact
Composer model id, or prove real-image write/shell denial. Keep Cursor disabled
for consumers.
