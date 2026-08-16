# Code Tribunal cleanup and consolidation implementation specs

- **Repository:** `seanleecoder/code-tribunal`
- **Baseline:** `main` at `451472d2ed0a8bc5d870409b224a69199570c843`
- **Intended destination:** `docs/improvement-specs/`
- **Status:** Ready for implementation handoff

## Purpose

This package converts the agreed cleanup and consolidation direction into individual,
implementation-ready specifications. It deliberately preserves the product features that
are required in production:

- four first-party reviewer adapters: Claude, Codex, OpenCode, and Cursor;
- one review stage and one critique stage;
- deterministic consensus over untrusted model output;
- GitHub and GitLab support;
- persistent cross-run finding state and thread reconciliation;
- secure revision binding, snapshot containment, credential isolation, and artifact
  validation.

The target product policy is informational:

- a finding is surfaced when at least two independent reviewers support it across direct
  review and critique;
- every anchorable surfaced finding is posted as a discussion thread;
- severity, including `blocker`, is an impact label only;
- Code Tribunal does not decide whether a change may merge;
- humans and downstream agents decide what to do after reading the threads.

## Spec set

| Spec | Title | Type | Depends on |
|---|---|---|---|
| [SPEC-54](spec-54-independent-support-informational-findings.md) | Independent-support informational findings | Behavior-changing cleanup | Current baseline |
| [SPEC-55](spec-55-publish-only-pipeline-no-merge-gate.md) | Publish-only pipeline with no merge gate | Behavior-changing cleanup | SPEC-54 |
| [SPEC-56](spec-56-first-party-reviewer-registry.md) | Static first-party reviewer registry | Consolidation | SPEC-54/55 config version coordination |
| [SPEC-57](spec-57-always-on-state-path-pruning.md) | Always-on state path pruning | Behavior-preserving cleanup | SPEC-54/55 config version coordination |
| [SPEC-58](spec-58-contract-oriented-test-consolidation.md) | Contract-oriented test consolidation | Behavior-preserving cleanup | SPEC-54 through SPEC-57 |
| [SPEC-59](spec-59-product-invariants-and-complexity-control.md) | Product invariants and lightweight complexity control | Governance | May land first |
| [SPEC-60](spec-60-critique-quality-observability.md) | Critique-quality observability | Non-blocking follow-up | SPEC-54 |
| [SPEC-61](spec-61-candidate-image-panel-canary.md) | Candidate-image four-seat panel canary | Release verification follow-up | SPEC-54 through SPEC-57 |

## Required implementation order

1. **Land SPEC-59 first or in parallel.** It records the product boundary before the
   cleanup changes contracts again.
2. **Implement SPEC-54 and SPEC-55 as one coordinated change series.** SPEC-54 defines
   `consensus.v2`; SPEC-54 through SPEC-57 together define `review_config.v3`, and SPEC-55
   defines the renderer over the new consensus shape. Do not merge an intermediate state in
   which the consensus schema no longer contains fields still consumed by the renderer, the
   gate, or posting. The renderer is the dangerous one: it reads the removed fields through
   defaulted lookups, so an intermediate state renders wrong output instead of failing.
3. **Implement SPEC-56 and SPEC-57.** These may be separate pull requests after the new
   policy contracts are stable.
4. **Implement SPEC-58 after the production seams stop moving.** It must delete obsolete
   tests rather than add a second suite beside them.
5. **Implement SPEC-60 and SPEC-61 as follow-ups.** Neither is a prerequisite for the
   cleanup. SPEC-60 is observational and must not change surfacing decisions. SPEC-61 is
   required before promoting or repinning a candidate image that changes reviewer-path
   behavior, but it is not a general pull-request gate.

## Cross-spec rules for coding agents

- Preserve the four adapters and critique. Optional means selectable by a deployment,
  not experimental.
- Do not introduce a dynamic plugin system.
- Do not preserve private Python imports, logger names, helper signatures, or test fixture
  internals for compatibility.
- Version public configuration and artifact shape changes explicitly. Do not change a
  versioned shape while keeping the old version identifier.
- Avoid long-lived old/new code paths. A migration message is preferable to a permanent
  compatibility adapter.
- Preserve all security boundaries around revision binding, no-follow snapshot traversal,
  external-fork secret refusal, provider credential isolation, endpoint validation,
  immutable dependency pins, and strict artifact validation.
- Do not add line-count, file-count, or test-count quality gates.
- Do not add exact-prose documentation tests.
- A new abstraction must either remove duplicated behavior immediately or establish a
  product boundary named in SPEC-59. Do not add framework layers for hypothetical future
  adapters, stages, platforms, or state backends.
- Run `make quality`, update workflow parity, regenerate golden artifacts where required,
  and execute the spec-specific negative tests before declaring a spec complete.

## Explicitly out of scope

The following existing proposals remain separate:

- SPEC-41 reviewer confidence defaulting;
- SPEC-43 trusted consumer image verification;
- SPEC-45 critique provenance display;
- SPEC-46 unanchored advisories;
- SPEC-47 trusted project review configuration;
- SPEC-48 auditable scope exclusions;
- SPEC-53 broader stringified structured-output normalization.

SPEC-42 becomes obsolete when SPEC-55 removes merge-gate semantics and must be deleted
from the open-spec index as part of that implementation.

Three of the specs that stay open also contain contract text that SPEC-54 and SPEC-55
invalidate, and must be reconciled rather than left to contradict the shipped runtime:
SPEC-45 and SPEC-46 both read `block_merge` and quorum as given inputs, and SPEC-48 specifies
a gate outcome that no longer exists. Reconciling them is part of landing SPEC-55, not a
follow-up.
