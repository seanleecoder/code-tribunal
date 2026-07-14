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
  review, critique, consensus, authenticated GitHub state-store posting, and
  the advisory gate.
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
- Final-head confirmation run:
  [29338660697](https://github.com/seanleecoder/code-tribunal/actions/runs/29338660697).
  Claude, Codex, and OpenCode reviews all succeeded; all three critiques,
  consensus, posting, and the advisory gate also succeeded.

Final verdict: accepted for workflow execution and authenticated state-store
posting. The installed GitHub workflow pulled the attested, digest-pinned images
and completed prepare, review, critique, consensus, state persistence, and gate
evaluation on the repository that publishes Code Tribunal.

## Enforcing-gate and inline-posting confirmation

- Hardened pull request:
  [#33](https://github.com/seanleecoder/code-tribunal/pull/33), head
  `c60007700ca9ba2239989e2c385322ab26c948b2`.
- Manual dogfood workflow run:
  [29345802433](https://github.com/seanleecoder/code-tribunal/actions/runs/29345802433).
- Image source commit:
  `e0ad996aafa40ceceb420014ce62a0e7b3105275`, using the attested digests
  recorded in `PHASE_5_5_ACCEPTANCE.md` under "Manual-dispatch images."
- Panel outcome: Claude, Codex, and OpenCode reviews succeeded; all three
  critique legs succeeded; consensus reported `panel_status=full` with no
  failed reviewers.
- Posting outcome: two human-facing inline review comments were created at
  exact diff anchors as
  [discussion 3580429451](https://github.com/seanleecoder/code-tribunal/pull/33#discussion_r3580429451)
  and
  [discussion 3580429525](https://github.com/seanleecoder/code-tribunal/pull/33#discussion_r3580429525).
  The authenticated `github-actions[bot]` state comment was also created as
  [issue comment 4971039322](https://github.com/seanleecoder/code-tribunal/pull/33#issuecomment-4971039322).
- Consensus surfaced the intentional advisory-to-enforcing default change as
  a non-blocking compatibility warning and the state-write verification as an
  informational security finding. Neither required acknowledgment.
- Gate outcome: success with `AI_REVIEW_MERGE_GATE_ENABLED=true`, confirming
  the shipped GitHub template evaluates the enforcing path.

Final live-posting verdict: accepted for inline review comments and the
machine-owned state store. A summary/review body is emitted only for findings
that cannot be anchored inline; that fallback remains covered by automated
tests rather than this exact-anchored live run.
