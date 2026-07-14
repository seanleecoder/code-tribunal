# Archived — Spend Enforcement

Status: paused indefinitely. Previously bundled into SPEC-17b.

## Intent

Provide real persisted per-job, per-change, and daily project spend accounting,
concurrency control, and an explicit enforcement policy. The former no-op
runtime and its skip status were removed because they did not enforce anything.

## Resume conditions

- Store, accounting unit, concurrency semantics, failure policy, and ownership
  are designed and reviewed.
- Enforcement is implemented and tested before any configuration is exposed.
- Artifact/schema compatibility is reviewed because the former placeholder
  status no longer exists.

This plan is independent of every other archived plan.
