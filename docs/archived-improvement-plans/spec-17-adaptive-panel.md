# Archived — Adaptive Panel

Status: paused indefinitely. Independent of the standalone reducer and external
integrations. Historical identifier: SPEC-17a.

## Intent

Reduce average model runs by starting with a cheaper deterministic first pass and
escalating to the existing full review-and-critique panel only when findings or
uncertainty justify it. The consensus decision policy would remain unchanged.

## Resume conditions

- A representative labeled corpus measures blocker recall, convergence, false
  negatives, run count, latency, and cost.
- Escalation triggers and failure behavior are deterministic and unit tested.
- Full-panel execution remains the default until the corpus shows no blocker
  recall regression.
- The new configuration is introduced only when implemented end to end; no
  reserved strategy key should be added in advance.

## Dependencies

The existing hermetic post→gate harness and labeled grouping corpus are useful
prerequisites. This plan does not depend on any other archived plan.
