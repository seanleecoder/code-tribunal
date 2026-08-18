# Code Tribunal cleanup and consolidation implementation specs

- **Repository:** `seanleecoder/code-tribunal`
- **Baseline:** `main` at `451472d2ed0a8bc5d870409b224a69199570c843`
- **Intended destination:** `docs/improvement-specs/`
- **Status:** SPEC-54 through SPEC-58 are implemented; SPEC-57 closed `review_config.v3`.
  SPEC-59 through SPEC-61 are pending implementation.

Sections below that describe SPEC-54/55/56/57/58 work state what was done, not what to do. Read
them as record; the shipped contract is the code, the tests, and
[`CHANGELOG.md`](../../../CHANGELOG.md).

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

The product policy this package targets is informational. SPEC-54 and SPEC-55 shipped it;
the remaining specs must not walk it back:

- a finding is surfaced when at least two independent reviewers support it across direct
  review and critique;
- every anchorable surfaced finding is posted as a discussion thread;
- severity, including `blocker`, is an impact label only;
- Code Tribunal does not decide whether a change may merge;
- humans and downstream agents decide what to do after reading the threads.

## Spec set

SPEC-54 and SPEC-55 landed as one change series and their requirement documents were
deleted, as [the improvement-spec index](../README.md) requires of every completed spec:
`git log` holds them, and the shipped contract is described by the code, the tests, and
[`CHANGELOG.md`](../../../CHANGELOG.md). Their rows stay in the table below because the
specs that follow state their dependencies in terms of them.

SPEC-56, SPEC-57, and SPEC-58 each landed as their own change series; their documents are still
here and will be deleted in a follow-up, once those series are on `main`. Deleting a document in
the same change that implemented it would leave no trace, because this repository squash-merges.
SPEC-59 through SPEC-61 remain plain text because their documents land separately.

| Spec | Title | Type | Depends on |
|---|---|---|---|
| SPEC-54 — **implemented** | Independent-support informational findings | Behavior-changing cleanup | Current baseline |
| SPEC-55 — **implemented** | Publish-only pipeline with no merge gate | Behavior-changing cleanup | SPEC-54 |
| [SPEC-56](spec-56-first-party-reviewer-registry.md) — **implemented** | Static first-party reviewer registry | Consolidation | SPEC-54/55 config version coordination |
| [SPEC-57](spec-57-always-on-state-path-pruning.md) — **implemented** | Always-on state path pruning | Medium cleanup; behavior-preserving for validated configurations | Implemented SPEC-54 through SPEC-56 `review_config.v3` baseline; landed before the first tagged v3 release |
| [SPEC-58](spec-58-contract-oriented-test-consolidation.md) — **implemented** | Contract-oriented test consolidation | Behavior-preserving cleanup | SPEC-54 through SPEC-57 |
| SPEC-59 — *pending, document not yet added* | Product invariants and lightweight complexity control | Governance | May land first |
| SPEC-60 — *pending, document not yet added* | Critique-quality observability | Non-blocking follow-up | SPEC-54 |
| SPEC-61 — *pending, document not yet added* | Candidate-image four-seat panel canary | Release verification follow-up | SPEC-54 through SPEC-57 |

## Required implementation order

Steps 1 to 4 are done. They are kept as record; step 5 is the live roadmap.

1. ~~**Land SPEC-59 first or in parallel.**~~ **Superseded.** SPEC-59 did not land first.
   SPEC-54/55 changed the contracts before the product boundary was recorded, so SPEC-59 now
   describes a boundary that already moved and must be rebased against the shipped
   informational contract before it is implemented.
2. ~~**Implement SPEC-54 and SPEC-55 as one coordinated change series.**~~ **Done.** They
   shipped together: SPEC-54 defined `consensus.v2` and SPEC-55 the renderer and the
   publish-only pipeline over it. The hazard this step warned about — an intermediate state
   in which the consensus schema had lost fields the renderer still read through defaulted
   lookups — was avoided by shipping them as one series, and is now spent. There is no merge
   gate left for a future step to coordinate with.
3. ~~**Implement SPEC-56 and SPEC-57.**~~ **Done.** They shipped as separate pull requests. The policy
   contracts they coordinate with — `consensus.v2` and `review_config.v3` — have landed on
   `main` and are stable in shape, but neither has appeared in a tagged release: the newest
   release still ships `review_config.v1`. `review_config.v3` is jointly defined by SPEC-54
   through SPEC-57 and closed when SPEC-57 landed. SPEC-54/55 removed `merge_gate`,
   `posting.fallback_to_summary_comment`, and `limits.max_posted_surface_findings`; SPEC-56
   appended the two reviewer keys and SPEC-57 appended `state.backend`, each to that same
   removal list rather than opening a new config version. SPEC-57, as the last
   config-changing spec, owns the final consolidated migration message.
4. ~~**Implement SPEC-58 after the production seams stop moving.**~~ **Done.** It deleted the
   replaced tests rather than adding a suite beside them: the per-provider copies of shared
   runner behavior, the duplicated endpoint and credential tables, and the image-time rerun of
   the checkout suite with its executed-test floor. The re-survey this step called for found
   section 2 already satisfied — landing SPEC-55 had collapsed the duplicated reducer-policy
   tests — so nothing was deleted for it; the reducer work was limited to renaming the suite
   that was still named after a spec phase.
5. **Implement SPEC-60 and SPEC-61 as follow-ups.** Neither is a prerequisite for the
   cleanup. SPEC-60 is observational and must not change surfacing decisions; its
   prohibitions on changing the `post` exit status and on restoring a merge gate describe the
   contract SPEC-55 established. SPEC-61 is required before promoting or repinning a
   candidate image that changes reviewer-path behavior, but it is not a general pull-request
   gate.

## Cross-spec rules for coding agents

- Preserve the four adapters and critique. Optional means selectable by a deployment,
  not experimental.
- Do not introduce a dynamic plugin system.
- Do not preserve private Python imports, logger names, helper signatures, or test fixture
  internals for compatibility.
- Version public configuration and artifact shape changes explicitly. Do not change a
  *released* versioned shape while keeping its version identifier. A version identifier that
  has not yet appeared in a tagged release is still being defined: a spec that this package
  names as a co-author of that version amends it in place, and the series takes one bump for
  the whole release rather than one per spec. `review_config.v3` is the current example —
  SPEC-54 through SPEC-57 jointly define it.
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

SPEC-42 became obsolete when SPEC-55 removed merge-gate semantics, and was deleted from the
open-spec index as part of that implementation. The human `wontfix` disposition it reasoned
about is unchanged and still supported.

Three of the specs that stay open also contained contract text that SPEC-54 and SPEC-55
invalidated — SPEC-45 and SPEC-46 read `block_merge` and quorum as given inputs, and SPEC-48
specified a gate outcome that no longer exists. They were reconciled as part of landing
SPEC-55, along with SPEC-47 and ADR-0002. No open specification now describes a consumer of
a deleted artifact.
