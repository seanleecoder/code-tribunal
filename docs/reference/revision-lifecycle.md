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
by fake-platform integration and posting tests; it still depends on the state
record being authentic and available.

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
operational failure consumed by the gate. Render-body version changes may cause
a one-time update of bot-authored comments without changing issue identity.
