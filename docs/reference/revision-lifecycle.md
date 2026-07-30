# Finding lifecycle across revisions

A surfaced concern is persisted as a finding record with an `issue_id`, anchor
context, rendered body hash, disposition, and platform discussion identity.
Later runs reconcile new consensus against those records.

## Normal progression

1. A new consensus group creates a new record and discussion.
2. An unrelated edit may move the line; context-based remapping retains the
   issue and updates its anchor.
3. A changed explanation updates the existing discussion when identity still
   matches.
4. Sufficient trustworthy reviewers can confirm absence and resolve the record.
5. Ambiguous or missing context becomes stale rather than being guessed.

Reruns against the same state and consensus are designed to update or skip the
same platform object rather than create a duplicate. This behavior is covered
by the GitHub and GitLab rerun cases in
[`test_post_gate_e2e.py`](../../ai-review/tests/integration/test_post_gate_e2e.py)
and the unchanged/update/remap cases in
[`test_post.py`](../../ai-review/tests/unit/test_post.py). It still depends on
the state record being authentic and available.

## Human dispositions

Authorized users may reply on the finding thread with a command on its own line:

- `/ai-review resolve`
- `/ai-review wontfix`
- `/ai-review reopen`

Resolving a thread only through the platform UI does not create the durable
`wontfix` disposition. Deleting the root GitHub review comment also removes the
identifier needed to resolve or reopen its thread through GraphQL.

## Retention and migrations

Open and `wontfix` records are retained by default. Bounded resolved and stale
record counts plus a total byte limit prevent unbounded state. Overflow is an
operational failure consumed by the gate. The posted-body format is now
`render-body.v4`. Existing bot-authored inline threads receive one content refresh
on the next run; issue IDs, markers, state records, and resolution status remain
unchanged. Summary notes update through their normal body-hash upsert. A rollback
causes one reverse refresh and does not discard state.

## Critique display budget

A group with critiques adds exactly one visible line: a counts line. Expanded
reasoning sits behind a collapsed disclosure, and what goes in it is budgeted by
verdict, because on an N-reviewer panel a group has up to N−1 eligible critics and
every rationale is a literal block:

| Effective verdict | Displayed as |
| --- | --- |
| `dispute` | Full rationale — the only verdict that should change whether a maintainer acts. |
| `noise` (group survived) | One line, elided to the shorter of its first sentence or 200 characters. |
| validated `duplicate` | Counts only. The group is already merged and the footer's reviewer list already names every reporter. |
| `agree` | Counts only, through the existing support counter. |

An invalid duplicate displays as a dispute, matching voting semantics rather than
the verdict the model requested. Full noise text and every duplicate rationale
remain in `critique_observations` and in the run artifact.

Majority-noise suppression is audited to `post_result.json` unconditionally and to
the merge-request summary only when `critique.show_disposition_audit` is `true` —
its audience is whoever tunes the panel, not whoever reviews the change, and
re-posting a suppressed report by default would undo the suppression it audits.
Summary entries carry `Found by`; inline bodies do not, because their consensus
footer already emits the identical reviewer list.
