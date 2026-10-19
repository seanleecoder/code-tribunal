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
[`test_publish_e2e.py`](../../ai-review/tests/integration/test_publish_e2e.py),
the cross-revision remap case in
[`test_revision_lifecycle_e2e.py`](../../ai-review/tests/integration/test_revision_lifecycle_e2e.py),
and the unchanged/update/remap cases in
[`test_post.py`](../../ai-review/tests/unit/test_post.py). It still depends on
the state record being authentic and available.

## Human dispositions

Reply on the finding's GitLab discussion or GitHub root inline review comment
with a command on its own line. GitLab requires Developer access or higher;
GitHub accepts Write, Maintain, or Admin.

- `/ai-review resolve`
- `/ai-review wontfix`
- `/ai-review reopen`

Resolving a thread only through the platform UI does not create the durable
`wontfix` disposition. Deleting the root GitHub review comment also removes the
identifier needed to resolve or reopen its thread through GraphQL.

## Retention and migrations

Open and `wontfix` records are retained by default. Bounded resolved and stale
record counts plus a total byte limit prevent unbounded state. Overflow is an
operational failure that makes the `post` job exit nonzero. Inline threads and
the summary note both carry a hash of their rendered text alone. When a release
renders an unchanged finding differently, each surfaced bot-authored inline
thread receives one content refresh on its next run; issue IDs, state records,
and resolution status remain unchanged. A rollback causes one reverse refresh
of surfaced threads and does not discard state; `wontfix` and resolved threads
are not re-rendered.