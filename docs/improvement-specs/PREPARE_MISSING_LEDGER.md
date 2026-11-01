# Fail earlier when durable review state is missing

Status: deferred compatible follow-up after 3.0; not a release blocker.
Decision: 2026-10-26, retained by the 2026-11-01 next-major scope decision.

Posting already fails closed when older bot history has no recoverable ledger.
Prepare currently discovers the missing ledger too late to avoid model work.
Before changing that behavior, specify the platform thread listing needed to
recognize the same repair-required condition during prepare.

Preserve the existing posting authority and missing-ledger repair procedure.
Reuse authenticated state/thread discovery; do not introduce a second state
backend, decoder or recovery policy. Initial empty changes and recoverable
same-revision interrupted publication must remain valid.

Acceptance: focused prepare tests on both platforms distinguish clean first runs,
recoverable interrupted writes and older missing-ledger history, and reject the
repair-required case before any model stage. Keep post's final fail-closed check.
