# Archived — External Issue-Tracker Integration

Status: paused indefinitely. Previously bundled into SPEC-17b.

## Intent

Add an optional issue-tracker adapter that discovers linked issues and performs
idempotent comment updates and carefully authorized transitions. The earlier
unwired client, config, state field, and post counters were removed.

## Resume conditions

- The platform abstraction, authentication boundary, idempotency model, and
  transition failure policy are approved.
- State and post-result schema changes include a migration/versioning decision.
- End-to-end tests prove comment creation, update, retry, and dry-run behavior.

This plan is independent of every other archived plan.
