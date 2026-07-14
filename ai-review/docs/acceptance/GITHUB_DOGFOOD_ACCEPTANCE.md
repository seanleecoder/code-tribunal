# GitHub Dogfood Acceptance Evidence

## Controlled pre-v0.4.0 run

- Status: accepted on 2026-07-14.
- Installed workflow merge commit:
  `f481e2b4fe70051d8e3499783ed8e14f5d350c86`.
- Trusted image source commit:
  `b79f29f69d053f87f1a205a82cefe0f3e1b93bef`.
- Base image digest:
  `sha256:d2a3fc87ac97aa9278a66669670e06d59b6bb5ae9db695836873b5f42892c7b0`.
- Reviewer image digest:
  `sha256:a6c112245c35e02a6f42001e5bf88578eabfd160a66a4e1e9552cba477e2478d`.
- Validation target: a same-repository draft pull request exercising prepare,
  review, critique, consensus, GitHub posting, and the advisory gate.
- Merge gate policy: advisory for the controlled run.
- Image publication and attestation run:
  [29337542439](https://github.com/seanleecoder/code-tribunal/actions/runs/29337542439).
- Controlled pull request:
  [#31](https://github.com/seanleecoder/code-tribunal/pull/31), head
  `bc97745c98309f225ce8b243b428980d131242b5`.
- Dogfood workflow run:
  [29338230558](https://github.com/seanleecoder/code-tribunal/actions/runs/29338230558).
- Review outcomes: Claude and Codex succeeded; OpenCode returned a classified
  `SchemaValidationError`, exercising the intended degraded-panel policy.
- Critique outcomes: Claude, Codex, and OpenCode succeeded, including the
  stage-specific critique status artifact paths.
- Consensus outcome: `panel_status=degraded`, with Claude and Codex recorded as
  successful reviewers, OpenCode recorded as failed, zero surfaced findings,
  and `block_merge=false`.
- Posting outcome: success with no warnings. Because consensus contained no
  findings, no human-facing review body was necessary; the authenticated
  `github-actions[bot]` state comment was created as
  [issue comment 4969958823](https://github.com/seanleecoder/code-tribunal/pull/31#issuecomment-4969958823).
- Gate outcome: success with `AI_REVIEW_MERGE_GATE_ENABLED=false`, confirming
  advisory operation for the controlled run.

Final verdict: accepted. The installed GitHub workflow pulled the attested,
digest-pinned images and completed prepare, degraded review, critique,
consensus, authenticated state posting, and advisory gate evaluation on the
repository that publishes Code Tribunal.
