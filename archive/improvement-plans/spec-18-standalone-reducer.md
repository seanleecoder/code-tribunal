# Archived — Standalone Deterministic Reducer

Status: paused indefinitely. Historical identifier: SPEC-18.

## Intent

Extract canonicalization, anchors, consensus, rendering, schema validation, and
their JSON schemas into an independently versioned package with no SCM, HTTP,
platform, CI-environment, or posting dependencies. Publish versioned finding,
critique, and consensus schemas with conformance examples.

## Resume conditions

- A real external consumer needs the package or interchange schemas.
- Package name, schema branding, semver policy, and release ownership are
  approved.
- An isolated test target proves the reducer imports and runs with SCM/HTTP
  modules blocked.
- The main engine consumes the reducer through the package boundary before any
  external publication.

## Dependencies

The completed typed/decomposed reducer work (SPEC-09, SPEC-13, and SPEC-14) is a
prerequisite. This plan does not depend on the adaptive panel or integrations.
