# Code Tribunal: aggressive simplification review

> Historical audit at the reviewed snapshot. The current dispositions and local
> completion status are in [AGENT_HANDOFF.md](AGENT_HANDOFF.md). Completed S02/S17
> work and partial S18/S19 work are no longer implementation instructions; S01
> follows the separately selected future-only 2.1.0 direction.

**Repository:** [seanleecoder/code-tribunal](https://github.com/seanleecoder/code-tribunal)

**Reviewed commit:** [`ec7e82754db062797a9c4d0646ced79ac2d8e7ff`](https://github.com/seanleecoder/code-tribunal/commit/ec7e82754db062797a9c4d0646ced79ac2d8e7ff), main on 3 October 2026.

**Purpose of this document:** a decision-ready, implementation-agent-ready removal plan. Recommendations intentionally favor a narrower product over preserving every existing option. They are proposals, not descriptions of changes already made.

## 1. Verdict

**Keep the tribunal. Delete much of the machinery surrounding it.**

The core is worthwhile and reasonably specific: heterogeneous coding agents independently examine a pinned change, critique findings, and feed a deterministic reducer that publishes corroborated concerns without deciding whether the change may merge. Persistent identities prevent repeatedly posting the same concern. GitHub and GitLab are both first-class delivery surfaces.

The biggest remaining simplification opportunities are not replacing four agents with one, deleting critique, or rewriting everything into one file. They are:

1. A release-certification/evidence subsystem that behaves like a second product.
2. Multiple representations and repeated validation of the same configuration, result, and state facts.
3. Model-facing contracts that ask the model to manufacture data the runtime owns.
4. Alternate execution paths, especially production mock fallback and generic output autodetection.
5. Thread lifecycle and consensus policies doing more than an informational review tool needs.
6. Tests and documentation rules that make incidental implementation details expensive to change.

My recommended end state is deliberately less capable in selected areas: no automatic resolution based on a finding's absence, no severity negotiation, no production mock mode, no general-purpose CLI-output salvage engine, no live streaming option by default, no legacy online state decoder after migration, and no custom release certificate/waiver database.

This is not a call to remove security boundaries or make errors look like clean reviews.

## 2. Scope, evidence, and limitations

I inspected the complete repository tree and read implementation and test code across configuration, adapter execution/output, input preparation, schemas, prompting, grouping, critique, consensus, persistent state, posting, CI templates, packaged smoke loading, documentation contracts, and schema/type alignment. I also read the current architecture, release process, compatibility register, product-boundary ADR, and open-work index. Large files were sometimes inspected in targeted ranges rather than completely.

**I did not obtain a runnable local checkout or execute the repository's test suite.** GitHub connector reads succeeded; local network/DNS access did not. This review does not certify current correctness, security, CI health, or live model compatibility. It also does not establish which deployments beyond the documented consumers depend on legacy behavior.

One independent experiment was executed: replacing grouping's union-find prepass with direct bucketed complete-link grouping produced the same result in **69,735 modeled cases**, including exhaustive small graphs and randomized larger graphs. The script and results are bundled. This supports the algorithmic argument only; it is not a test run against the repository.

Source links below are pinned to the reviewed commit. Where a test file is named as a removal target, that is an instruction to inspect and classify its assertions, not a claim that every assertion in the file is unnecessary. No percentage reduction or runtime improvement below is presented as measured.

## 3. What the product actually does

### Current functional path

```text
Trusted preparation
  Pin the change revision; obtain diff and repository snapshot;
  load configuration, trusted rules, and previous platform state.

Independent review
  Run enabled seats from Claude / Codex / OpenCode / Cursor.
  Normalize findings and record reviewer execution health.

Critique
  Offer pooled findings to peer critics, normally with reviewer aliases.
  Collect support, disagreement, noise, duplicate, and severity suggestions.

Deterministic reduction
  Group related findings, apply unique-identity support rules,
  incorporate critique, and currently consult historical state identity.

Publication and persistence
  Reconcile threads, render inline comments or summary fallback,
  process human dispositions, update platform-hosted state.
```

The current configuration permits disabling critique, although the accepted product envelope describes one review round followed by one critique round. Current decision policy also has exceptions to the simple two-supporter description: ambiguous state identity forces FYI, and majority noise can drop a group before the support threshold is considered. Those are important distinctions when changing behavior. [Core/ADR][s-adr] [Config][s-config-yaml] [Policy][s-policy]

### Preserve these capabilities

- All four existing first-party adapters, including Cursor; three or four enabled seats; configurable model and supported effort choices.
- Independent review and one bounded blind critique round, with a deterministic reducer as authority.
- Support counted by unique reviewer identity across review and critique, never by the number of messages or repeated findings.
- GitHub and GitLab, inline findings, and a visible fallback when inline placement is unsafe or unavailable.
- Persistent thread identities and human dispositions across runs. Simplify their representation and lifecycle rather than deleting memory.
- Trusted rules, pinned source revisions, and honest execution-health reporting.
- Informational output: finding severity does not decide merge eligibility.

### Do not re-propose work already completed

Semantic-similarity grouping, merge gating, recursive/multiple critique rounds, dynamic adapter configuration, and a configurable state-backend family have already been removed or excluded. Shared shell scaffolding already exists in `common.sh`. The GitHub workflow copy already has a canonical source and parity check. Do not call these undiscovered duplications or sell their existing consolidation as new work. [Configuration removals][s-config] [Grouping][s-grouping] [Shared adapter code][s-common] [ADR][s-adr]

## 4. Target architecture

Keep a few explicit boundaries rather than imposing a new framework:

```text
prepare -> immutable RunContext
            |
            +-> reviewer adapters -> ReviewerResult per seat and stage
                                      |
                                      v
                                 pure reducer
                                      |
                                      v
                              report + publisher
                                      |
                                      v
                              lean thread ledger
```

`RunContext` means one resolved configuration and revision identity, not a new orchestration service. `ReviewerResult` means one authoritative success/failure/data envelope, not another artifact alongside the existing ones. The thread ledger remains platform-hosted.

Keep the existing useful module boundaries: platform transport, pure grouping/consensus, presentation, and network mutation. A small pure helper module is not overengineering merely because it is small. The danger is having several authorities for one fact or several policies for an unnecessary feature.

## 5. Prioritized removal portfolio

**Gain** is expected maintenance/clarity benefit, not measured performance. **Risk** is implementation and product-regression risk. **Scope** is relative change breadth: S = localized, M = several coordinated modules, L = cross-boundary migration. Items sharing files have overlapping gains; do not add their estimates.

| ID | Recommendation | Gain | Risk | Scope | Nature |
|---|---|---:|---:|---:|---|
| S01 | Retire bespoke release certificates, evidence ledgers, and waivers | Very high | High | L | Operational contract change |
| S02 | Delete documentation-policy machinery and its meta-tests | High | Low-medium | M | Tooling deletion |
| S03 | Remove every production mock/fallback path | High | Medium | M | Runtime narrowing |
| S04 | Resolve configuration once; remove non-choices and safety-off switches | High | Medium | M | Configuration change |
| S05 | Shrink the model-facing finding and critique contracts | Very high | Medium-high | M | Model contract change |
| S06 | Replace result/status duplication with one result envelope | High | Medium-high | L | Artifact contract change |
| S07 | Use Python as the sole adapter launcher/configuration writer | High | High | L | Execution refactor |
| S08 | Replace generic output autodetection with explicit pinned transport codecs | High | High | M | Compatibility narrowing |
| S09 | Delete opt-in live mirroring and thread-pumped log buffering | Medium | Medium | M | Operational feature deletion |
| S10 | Delete grouping's redundant union-find prepass | Medium | Low | S | Intended equivalent refactor |
| S11 | Make corroboration informational; remove noise veto/severity negotiation | High | Medium-high | M | Intentional policy change |
| S12 | Stop maintaining a second schema interpreter in tests | High | Medium | M | Contract simplification |
| S13 | Remove automatic absence-based thread resolution | High | High | L | Intentional lifecycle change |
| S14 | Narrow state matching, recovery, and legacy format support | High | High | L | Stateful migration |
| S15 | Render at publication only; default to single-line inline comments | Medium-high | Medium | M | Presentation simplification |
| S16 | Simplify CI without collapsing credential boundaries | Medium-high | Medium-high | M | Workflow refactor |
| S17 | Replace the packaged smoke manifest framework with explicit probes | Medium | Low-medium | S-M | Test tooling deletion |
| S18 | Prune tests by contract ownership; keep standard quality tools | High | Medium | M | Test-suite redesign |
| S19 | Reduce policy/docs and freeze speculative product expansion | Medium-high | Low-medium | M | Scope reduction |
| X01 | Optional later: group once before critique; remove duplicate arbitration | Potentially high | High | L | Experimental quality tradeoff |

The recommended program is S01-S19, sequenced below. X01 is not required to obtain the main gains and should not block the other deletions.

## 6. Detailed work items

### S01 - Replace the release-certification subsystem with an ordinary release pipeline

**Evidence.** The documented process coordinates runtime commit R, release commit P, image digests, generated/manual evidence records, source bindings, waiver declarations and markers, a signed manifest checksum, tag-specific code execution to rebuild certificates, publication retries, and frozen note bodies. Several substantial scripts exist solely to create or validate this structure. The existing live campaign is already impact-scoped and not an ordinary-PR gate; the problem is the bookkeeping around it, not a claim that every release blindly runs everything. [Release process][s-release] [Makefile][s-make]

**Delete/simplify.** Retire the custom certificate/rebuild protocol, source-controlled evidence-waiver ledger, evidence-record generator, and elaborate draft/active release-input state. Preserve immutable images, dependency pins, ordinary provenance, protected release authorization, and a small set of useful canaries.

**Concrete replacement.** Build from R, run image smoke tests, run affected live canaries, and promote those exact tested digests. Initially keep the existing R-to-P pin update as a short release procedure; do not invent a new distribution mechanism merely to eliminate a second commit. Retain one small check that runtime inputs did not change between the build source and the pin-only release commit. Publish notes and build identity from the workflow. Do not execute historical tag-specific release tooling to reconstruct a custom certificate. A later move to generated release-template assets is optional and would require an explicit installation-contract change.

**Targets.** `release_finalize.py`, `build_release_manifest.py`, `check_release_manifest.py`, `check_release_inputs.py`, `canary_evidence_records.py`, their release-workflow consumers, and corresponding tests. Extract anything still needed before deleting a whole file. Reduce, rather than blindly delete, candidate and platform lifecycle probes.

**Gain.** Removes an entire secondary state machine and the tests/docs needed to police it. Makes a routine runtime fix less likely to require release-governance changes.

**Risk.** Weaker custom audit evidence and a changed publication contract. Existing consumers may depend on assets. Do not mutate historical releases or claim old attestations were preserved. Announce the cut, preserve old tags/assets, test one complete new release path, and document exactly which provenance guarantees remain.

**Accept when.** A maintainer can trace source -> tested digests -> consumer pins from one workflow run; both platforms consume the intended images; no former certificate/waiver path remains required for a new release.

### S02 - Delete the custom documentation-policy product

**Evidence.** `test_docs_contract.py` tests Markdown parser edge cases, exact section placement/counts, configuration/environment inventories, particular workflow strings, and a literal action revision. `check_docs.py` and the Markdown-link wrapper support these conventions. [Documentation tests][s-doc-tests] [Makefile][s-make]

**Delete/simplify.** Remove prose-layout, exact-heading, frozen-note-byte, inventory-row, and exact-source-string enforcement. Remove release-note link rewriting conventions made unnecessary by S01. Keep ordinary links and a small number of executable installation/configuration examples. Use an existing link checker directly, ideally as a separate non-product check, rather than maintaining a custom downloader/parser/convention layer.

**Gain.** Documentation becomes editable without repairing a second program that recognizes its formatting. Removes tests of tests of conventions.

**Risk.** Broken links or undocumented options can slip through. Mitigate with one reference location for the much smaller configuration surface and executable examples. Preserve real install/security checks currently co-located in documentation tests; migrate those assertions before deleting their host file.

**Accept when.** Rewording a paragraph or renaming a heading cannot fail runtime quality checks; a broken installation example still can. No replacement documentation framework is introduced.

### S03 - Delete production mock execution and fallback

**Evidence.** `common.sh` can fall back to a mock when a binary or credential is missing unless the seat requires real execution. Additional authorization gates prevent an unguarded fallback; equivalent checks also live in Python and CI. This is not a claim that production currently defaults to silently using mocks. [Shared shell][s-common] [Runner][s-runner] [Process controls][s-process]

**Delete/simplify.** Remove `AI_REVIEW_LOCAL_MOCK`, `AI_REVIEW_ALLOW_LOCAL_MOCK`, per-seat `REQUIRE_REAL` controls, mock-scenario forwarding, fallback/refusal parsing, and `mock_reviewer` from the production execution path. Missing binary/key always produces an explicit failed result.

Tests can inject recorded results through test-only code. A separate local demo can run deterministic fixtures; it must not be selectable from production environment variables. Make that demo show at least two supporting identities and a critique instead of merely one review that cannot demonstrate corroboration.

**Gain.** Deletes a substantial combinatorial mode matrix while strengthening the meaning of a production review.

**Risk.** Existing image/canary/local harnesses use mocks and must move to a separate test entrypoint. Deterministic lifecycle testing is still valuable; do not delete it or label it real-model evidence.

**Accept when.** Production cannot produce mock findings regardless of environment settings; missing prerequisites fail visibly; the offline harness remains available without production fallback code.

### S04 - Resolve configuration once and remove options that should be invariants

**Evidence.** `load_config` reapplies environment overrides; prepare, runner, prompt rendering, and consensus resolve configuration repeatedly. A hand-written effective-config projection and digest then detect cross-job disagreement. The YAML exposes multiple policy and safety choices that need not be choices. [Config][s-config] [Prompting][s-prompts] [Consensus][s-consensus]

**Delete/simplify.** Trusted prepare resolves and validates configuration once. Downstream stages consume the exact immutable resolved configuration; they do not reinterpret environment variables. Preserve run/config identity at transport boundaries, but delete competing re-resolution and hand-maintained summary projections.

Keep a small operator surface: roster, per-seat model/effort, sensible common review/critique budgets, and truly deployment-dependent resource limits. Keep provider credentials outside serialized configuration. Preserve support for enabling Cursor deliberately.

Hardcode: one blind critique round, stale-head protection, forbidden external-fork secrets, supported inline sides, and the selected state format/integrity policy. Remove `allow_severity_downgrade`; remove the resolution-threshold setting under S13. Fixed adapter paths/credential names remain trusted registry data, as they already are.

Stop requiring each disabled seat to repeat a complete configuration. Resolve shipped defaults plus explicit selected-seat overrides into a complete internal roster. Remove detailed old-version migration/tombstone tables in the breaking release; retain one actionable unsupported-version/unknown-key error and migration instructions. Do not silently ignore old reserved settings.

**Gain.** One source of truth, fewer invalid combinations, fewer cross-stage hashes to reconcile, shorter YAML and docs.

**Risk.** Breaks existing variable/configuration usage. Frozen context must originate in trusted preparation; serializing attacker-controlled configuration does not make it trusted. Keep strict unknown-key and value validation at the actual entry boundary.

**Accept when.** Changing a downstream environment override cannot change this run's policy; secrets never enter artifacts; old settings produce a clear migration failure rather than a silent no-op.

### S05 - Stop asking models to manufacture runtime-owned metadata

**Evidence.** The raw finding schema requires `context_hash`, `hunk_header`, old/new paths, nested old/new line structures, a `line_code` fixed to null, and numeric confidence. Finalization computes context hashes and line codes itself. Critique targets are long hash IDs; finalization contains specific handling for well-formed but unknown IDs miscopied by models. Missing finding confidence is an explicitly open correctness problem. [Raw schema][s-raw-schema] [Finalization][s-schema] [Open work][s-open]

**Delete/simplify.** Give models a compact authoring schema: location, severity/category, title, explanation, evidence, optional suggestion. Derive GitLab/GitHub anchor details, hashes, fingerprints, run IDs, and timestamps in trusted code. Do not put those fields in the model contract.

Delete numeric confidence rather than merely defaulting it: remove confidence-based representative/cap tie-breaking and select deterministically using impact plus stable IDs. This is an intentional ranking change, not a behavior-preserving refactor. Keep the per-reviewer finding cap and visible invalid/truncated-result diagnostics.

For critique, expose short pool-local IDs such as `F001`, mapping them back through the trusted pool manifest. No cross-run authority is attached to the short ID. Unknown IDs still fail validation or are explicitly counted as invalid feedback. Minimize the prompt manifest to the context reviewers need rather than dumping internal bookkeeping.

**Gain.** Fewer schema failures, less normalization scaffolding, less prompt/output bookkeeping, and elimination of a known required-field failure mode. No measured token or quality saving is claimed until tested.

**Risk.** Anchor mapping can become ambiguous; never guess a line or trust a model-supplied hash. Confidence removal changes tie-breaking and requires a versioned artifact update where the field is public. Short-ID mapping must remain bound to the run and exact pool.

**Accept when.** All four adapters can return valid findings with no hashes/confidence/line-code placeholders; added, removed, unchanged and renamed-file locations map correctly; malformed output is never counted as a clean review.

### S06 - One result envelope instead of a batch and a status mirror

**Evidence.** The runner writes both finding/critique batches and separate status artifacts, repeating run identity, status, configuration identity, and quality counts. It has status-only and empty-batch failure variants plus nested fallback handling when a digest cannot be resolved. Consensus persists both `successful_reviewers` and an identical `resolution_eligible_reviewers` list. [Runner][s-runner] [Schema][s-schema] [Consensus][s-consensus]

**Delete/simplify.** Replace those mirrors with one `ReviewerResult` per seat and stage: trusted identity, status, redacted error, timing, data, and enough raw/invalid/truncation facts to explain quality. Compute accepted count from data length. Derive success and policy eligibility instead of persisting both lists and a boolean that must agree with several counts.

The trusted orchestrator knows the expected roster. A missing result is a transport/execution failure, not a successful empty result. Avoid synthesizing apparently successful placeholders. Preserve valid partial output only with an explicitly non-clean/incomplete status.

**Gain.** Fewer files, schema families, writers, consistency checks, and fallback branches. S13 also removes much of the reason to carry resolution-eligibility metadata.

**Risk.** Cross-stage artifact migration and missing-artifact handling. Same-shape data from another run/seat must remain rejected. A single envelope is not a reason to trust its self-reported identity without comparison to the expected job/run.

**Accept when.** Each stage has exactly one authoritative outcome; genuine empty success, all-invalid output, timeout, partial output, and missing transport remain distinguishable; stale and wrong-seat results are rejected.

### S07 - Use Python as the sole execution/configuration layer

**Evidence.** Python starts shell adapters; shared shell code handles paths/mocks/snapshots; OpenCode builds a large interpolated JSON configuration and invokes Python again. OpenCode also supports ambient-vs-packaged binary resolution. [Runner][s-runner] [Common shell][s-common] [OpenCode adapter][s-opencode]

**Delete/simplify.** Keep four explicit adapters but implement their argv, environment, and configuration construction in Python using argument arrays and a JSON serializer. Share only the lifecycle/snapshot/environment code that actually repeats. Use fixed trusted binary locations for the supported container runtime; local tests inject dependencies outside the production path. Do not add a plugin manager, adapter inheritance framework, or dynamically loaded provider registry.

**Important exception.** Keep the OpenCode server client until the pinned CLI demonstrably supplies the required structured-output behavior another way. The source explicitly explains why `run --format json` is not a schema-enforcement replacement. Preserve the explicit `StructuredOutput` and `external_directory` permissions and current project-configuration stripping.

**Gain.** Deletes cross-language quoting and environment protocols, removes injection hazards caused by interpolating model strings into JSON, and centralizes actual process ownership.

**Risk.** Very high adapter compatibility sensitivity. Credential sanitization must not disappear along with redundant shell `env -i` calls. Some adapter-specific flags are essential current behavior, not historical cruft.

**Accept when.** Container tests verify exact argv/config/env behavior and representative live runs succeed for every changed adapter, including Cursor. No reviewer receives posting credentials or another seat's credential.

### S08 - Replace universal parser autodetection with explicit transport codecs

**Evidence.** `adapter_output.py` recognizes plain JSON, fences/prose, stream envelopes, result envelopes, structured results, stringified lists/items, and optional-stage root inference. It includes custom rules for bracketed prose and stray JSON closers. Several fallbacks document live failures. [Output parsing][s-output]

**Delete/simplify.** Select the codec explicitly from the adapter and its pinned invocation, not by guessing from arbitrary stdout. Each codec extracts that transport's final answer; one shared stage-specific payload validator then validates it. Require `stage`; delete stage-less inference. Do not recursively mine arbitrary text/metadata for something shaped like findings.

Retain a small, documented fallback only where a captured trace from a currently supported CLI proves it is needed. Remove old transport forms when their pinned CLI is retired. Enforce one unambiguous payload rather than continually extending a mini-language for prose around JSON.

**Gain.** Smaller failure surface and easier debugging: transport failure versus malformed model answer is explicit.

**Risk.** Rejecting usable current output increases model failures and cost. Do not simply demand raw JSON from all four agents or delete fixtures because they are inconvenient. Do not scrape reasoning/tool messages as final answers; retain duplicate-key rejection.

**Accept when.** A corpus of current pinned-CLI traces passes; obsolete formats fail clearly; no heuristic silently chooses between conflicting payloads.

### S09 - Remove optional live mirroring and its stream-pump machinery

**Evidence.** `_run_adapter_process` maintains two pump threads, unbounded in-memory line collections, optional line-by-line redacted mirroring, and different cleanup/join paths. [Process execution][s-process]

**Delete/simplify.** Default to file-backed stdout/stderr in a controlled temporary directory, with a bounded redacted diagnostic tail after completion or failure. Remove the live-mirroring option and thread-pump path. Keep process-group creation and termination, stage deadlines, cleanup, and practical disk/output limits. A plain `subprocess.run(timeout=...)` is not automatically a correct replacement for killing descendant processes.

**Gain.** Fewer concurrency/error paths and less memory buffering. The product need not be an interactive log viewer.

**Risk.** Less live visibility during long model runs; a file-backed design needs bounded storage and careful redaction before upload. Actual performance is unmeasured.

**Accept when.** A fixture that spawns a child holding output open cannot hang timeout cleanup; diagnostics remain useful; sensitive raw output does not become a default CI artifact.

### S10 - Delete union-find from grouping without changing grouping semantics

**Evidence.** `group_findings` first forms all-pairs connected components with union-find, then buckets by path/category and runs `_split_transitive_component`, which requires each new member to match every existing group member. [Grouping][s-grouping]

**Change.** Bucket the stably ordered findings by `(category, anchor_path_key)` and run the same deterministic greedy complete-link split directly. Preserve `same_issue`, valid duplicate links, sorting, and final output order.

```python
# Conceptual replacement, not an untested drop-in patch.
buckets = bucket_by_path_and_category(sorted_findings)
groups = []
for bucket in buckets:
    groups.extend(existing_complete_link_split(bucket, duplicate_links))
return sorted(groups, key=lambda group: group[0]["source_finding_id"])
```

**Why equivalent.** Findings in distinct connected components cannot match each other, so they cannot share a complete-link group. The connected-component prepartition does not change which compatible group is encountered within a component. Path/category bucketing is retained explicitly.

**Verification.** The bundled independent model found no differences in 69,735 cases. It assumes a symmetric matching relation and identical stable ordering/bucketing/splitting. Run the actual grouping fixtures, transitive-split golden, duplicate-link tests, and a differential check against the previous implementation before merging.

**Gain/risk.** A concrete redundant algorithm is deleted, not merely moved. Low risk relative to the other items. Do not replace the existing clique-like split with connected components alone: that would change semantics and allow transitive false merges.

### S11 - Align decision policy with informational corroboration

**Evidence.** `decide_group` checks ambiguity, then majority noise, then the support threshold. `critique.py` has a noise-vote majority, severity downgrade controls, a no-crossing-blocker rule, and strongest-verdict collapse. Consequently, two direct supporters are not always sufficient under the current implementation. [Policy][s-policy] [Critique][s-critique]

**Delete/simplify.** Adopt an explicit policy: a concern with at least two unique independent supporters is reported; disagreement accompanies it. Remove the independent noise veto and severity downgrade machinery. Lower-support findings may remain in the report artifact rather than filling public summaries by default. Never cap or silently hide corroborated findings merely to reduce comment volume.

Thread-identity ambiguity changes placement/mutation permission, not evidential support: report the corroborated concern in a non-mutating summary fallback without guessing which historical thread to update. Preserve dissent and one effective contribution per reviewer identity.

Initially retain existing grouping and source-level critique mechanics. Do not combine this policy change with X01's pre-grouping redesign.

**Gain.** One understandable decision rule, fewer special statuses, and no faux merge-safety protection around an informational severity label.

**Risk.** More contested/noisy concerns may be visible. This is an intentional product change: update policy docs, artifact constraints, and goldens rather than disguising it as an internal cleanup. A critic whose review stage failed must still be able to contribute valid independent critique.

**Accept when.** Tests explicitly cover two supporters plus dissent, self-critique, repeated critiques from one identity, failed initial review with successful critique, and ambiguous historical thread placement.

### S12 - Delete redundant schema machinery, not boundary validation

**Evidence.** `test_types_schema_alignment.py` implements a generic reflection-based TypedDict/JSON-Schema checker with alias, union, nullability, reference, requiredness, and enum logic. Finalization constructs a synthetic one-finding batch to validate each item, validates the full batch, and the runner validates again. `validate_instance` reloads the schema and constructs a validator each time. [Alignment tests][s-type-tests] [Schema implementation][s-schema] [Runner][s-runner]

**Delete/simplify.** Keep JSON Schema as the existing authority at actual model/file boundaries; use ordinary typed internal objects without demanding a second structurally identical hand-maintained wire contract. Remove the custom schema interpreter and alignment meta-tests after defining those boundaries clearly. Prefer explicit conversion/round-trip examples over implementing another schema standard in the test suite.

Validate a finding using its item schema, not a fabricated entire batch. Cache validators per process. Validate once at admission; validate again only when crossing a genuinely untrusted persisted/CI transport boundary, not after every trusted function call. Remove schemas for deleted status/projection artifacts under S06 rather than renaming them.

**Gain.** Removes a partial type-system implementation and repeated ceremony around each finding.

**Risk.** Deleting alignment checks while retaining two independently edited mirror contracts would merely hide drift. Do the ownership change first. Introducing Pydantic or code generation is optional only if a small proof shows greater net deletion; it is not part of this recommendation by default.

**Accept when.** Malformed external payloads are rejected, internal refactors do not require editing a reflection framework, and there is one authoritative definition per boundary shape.

### S13 - Delete automatic absence-based resolution

**Evidence.** `_plan_stale_records` changes an open finding to resolved when it is absent from a later run and a resolution quorum exists; otherwise it becomes `stale_unverified`. Related state, configuration, counters, and publication logic maintain this behavior. [State planning][s-state-plan] [Posting][s-posting]

**Delete/simplify.** Stop treating failure to re-detect a concern as proof it was fixed. Keep a lean state model centered on active findings and explicit human dispositions. An absent finding becomes not-seen-this-run metadata, not a resolve/unresolve API action. Remove the resolution quorum configuration/list, automatic stale-to-resolved transitions, and policy states existing solely for them.

Remove accumulating `run_history` from the state payload; the CI platform already owns run history. Retain only last-seen/run identity needed for reconciliation. Preserve human resolve/wontfix/reopen behavior in the first migration; deleting the command interface is not required for these gains.

**Gain.** Deletes a whole inferred lifecycle and makes correctness easier to explain. Missing/partial reviewer output can no longer incorrectly imply remediation through this path.

**Risk.** Threads remain open until a human handles them; some users value auto-resolution. This deliberately removes that convenience. Existing state must migrate without reopening human-dismissed threads or discarding dispositions.

**Accept when.** Absence, degraded runs, empty valid output, and cap-truncated output never automatically resolve threads; explicit authorized human dispositions still persist and apply idempotently.

### S14 - Narrow state matching and support only one current state format

**Evidence.** Memory supports current and legacy note encodings, wrapper and payload hashes, several projections, permissive record normalization, five matching precedence levels, retention options, and discussion-marker recovery. The compatibility register lists config tombstones but not the legacy note decoder. [Memory][s-memory] [Compatibility register][s-compat] [State planning][s-state-plan]

**Delete/simplify.** Preserve exact persisted identity and the minimum context/path-based fallback needed for real revision continuity. Remove weak title/symbol fallback heuristics after replaying historical fixtures; prefer an explicit unmatched or ambiguous outcome over confidently mutating the wrong thread. Delete per-status retention knobs; preserve active/human-dismissed records and a small fixed bound appropriate to the platform's note size.

Migrate the legacy wire encoding once, outside ordinary runtime execution, then delete its online decoder. Never implement this as silently ignoring old notes and starting empty. Remove optional checksum-disable behavior; keep one integrity representation and bot-author verification. Hashes detect corruption, not authenticity.

Separate missing initial state from corrupt/inaccessible existing state. The latter should not silently become an empty ledger. Retain minimal authenticated marker reconciliation for interrupted publication; move broad reconstruction of a missing ledger into an explicit repair operation. Do not remove crash-recovery/idempotency semantics simply because a recovery flag disappeared.

**Gain.** Fewer identity heuristics and state permutations; narrower compatibility commitments.

**Risk.** High: overly aggressive cuts can duplicate threads or lose human dismissals. Build a one-time export/migration path, replay the lifecycle fixtures, and report migration ambiguity without writes. Do not build a permanent migration framework.

**Accept when.** Old supported notes migrate demonstrably; human dispositions and stable thread IDs survive; wrong-author markers never gain authority; failures after partial publication can be reconciled safely.

### S15 - Render only after placement is known; remove multiline retries

**Evidence.** Consensus renders platform-specific bodies to calculate hashes. Posting renders again after matching/remapping, and supports multiline placement plus a single-line retry/fallback path. `write_persisted_state` creates a note and updates it again to embed its own platform-assigned ID. [Consensus][s-consensus] [Posting][s-posting]

**Delete/simplify.** Keep consensus presentation-independent. Match identity, choose placement, then render once; update stored body hashes only for successful publication. Keep content-based idempotency and a stable ownership marker, not platform presentation fields embedded in consensus output.

Choose single-line inline comments, with any additional range shown as text or links. Retain old/new/unchanged-side support and summary fallback. Delete the multiline option and retry ladder rather than supporting every platform formatting capability.

Treat `state_note_id` as transport metadata obtained from the platform, not self-referential payload data. Remove the create-then-update write solely to persist the note's own ID, while preserving validated discovery on later runs.

**Gain.** Fewer render/hash passes, less cross-platform placement logic, and one fewer initial state write.

**Risk.** Less polished range highlighting and potential body-format refreshes. Existing thread ownership and update detection must remain correct. Hashing the wrong representation can cause needless updates or missed changes.

**Accept when.** Pure consensus has no render/platform dependency; unchanged visible content does not trigger needless updates; unplaceable supported findings remain visible; state can be found without a self-embedded ID.

### S16 - Reduce workflow complexity without flattening trust boundaries

**Evidence.** Both platforms fan out four statically declared seats, including disabled seats that emit skipped outcomes. GitLab has additional critique-enabled/source-gating combinations; GitHub maps configuration across all jobs and downloads separate status/batch artifact trees. Their terminal-job semantics intentionally differ. [GitHub template][s-gh-ci] [GitLab template][s-gl-ci]

**Delete/simplify.** Remove production mock variables, repeated environment policy resolution, optional-critique branching, and artifact paths retired by S03/S04/S06. Keep a static four-seat matrix unless a tiny platform-native enabled-seat matrix clearly reduces total complexity. Generating a dynamic GitLab child pipeline just to save one skipped job is not the recommended simplification.

Consider combining deterministic reduction and publication into one trusted base-image job, with a pure reducer function and posting credentials exposed only where needed. This removes one artifact/job boundary, not reviewer isolation. Keep serialized per-change publication and stale-head checking. Do not force identical failure semantics onto GitHub and GitLab.

For GitHub's canonical template plus installed copy, the existing small sync/parity approach is acceptable. Remove it only as part of a proven simpler installation change; do not introduce a workflow generator or symlink-based assumption merely to eliminate one file.

**Gain.** Smaller DAG and fewer status/configuration combinations without removing platform support.

**Risk.** CI success/skipped/allowed-failure semantics are easy to break, and consumers may reference job names. Credentials must not become reachable by reviewer processes. Do not claim environment scrubbing fixes an attacker-controlled secret-bearing pipeline definition; the repository explicitly records that trust limitation.

**Accept when.** Failure of one reviewer permits remaining evidence, total failure is visible, critique failure does not invent support, stale runs do not post, and manual/automatic/fork behavior works on each platform.

### S17 - Replace the smoke-suite manifest framework with explicit smoke probes

**Evidence.** The packaged smoke loader dynamically resolves test IDs, discovers defined cases separately, and checks declared/loaded/defined sets for exact equality. A second manifest owns the list of tests that the test files already own. [Smoke loader][s-smoke]

**Delete/simplify.** Keep packaged-runtime verification, but express it as a small explicit probe entrypoint per image scope or normal test loading with a nonzero-case assertion. Remove custom reflection, exact test-ID inventories, and manifest-parity tests. Renaming a private test should not be a distribution-contract change.

**Gain.** Deletes a framework designed to guard another framework while keeping the useful image checks.

**Risk.** An empty collected suite must not pass. Avoid shipping the whole repository test suite as the alternative. Base-image and reviewer-image checks test genuinely different packaging properties and should remain distinguishable.

**Accept when.** Missing runtime resources/binaries fail image checks, no tests collected fails, and adding or renaming a probe does not require syncing an unrelated identity registry.

### S18 - Prune tests by behavior ownership, not by a target count

**Evidence.** Tests include valuable hostile-input/lifecycle/consensus cases alongside exact docs/source checks, a generic schema alignment engine, release-governance tests, and smoke-manifest contracts. Standard Ruff, pytest, and strict mypy are already simple tools. [Docs tests][s-doc-tests] [Type tests][s-type-tests] [Tool config][s-tools]

**Delete/simplify.** Delete tests of removed features in the same commit as the feature. Replace tests asserting private helper paths, exact import inventories, duplicated declarations, and incidental prose with tests of outcomes where an outcome still matters. Keep a small number of architecture checks only for real trust/purity boundaries, not an exact module layout.

A single behavioral scenario should have a clear owner: normalization behavior in parser tests, grouping in reducer tests, thread reconciliation in lifecycle tests, transport semantics in platform tests. Integration tests connect those boundaries; they need not repeat every scalar validation case.

Keep Ruff, pytest, and mypy. Simplify `make test` to one authoritative pytest command. Remove the redundant `test-strict` indirection and standalone compile pass after confirming no otherwise-unchecked script depends on it. Coverage may remain an optional diagnostic; there is no evidence here of a numeric coverage gate that needs to be removed. Do not invent one.

**Gain.** Frees implementation agents to actually delete behavior instead of preserving it to satisfy historical assertions.

**Risk.** Some ugly tests exist because of real production failures. Inspect their rationale and fixtures before deleting. Fewer tests are not automatically better; fewer duplicate authorities are.

**Accept when.** Every retained behavioral contract has an owner, intentionally retired behavior has no zombie tests, and the safety/replay suite below remains meaningful and green.

### S19 - Keep product invariants, not permanent implementation mandates

**Evidence.** ADR-0003 sensibly excludes speculative product dimensions, but also enshrines the release-input/evidence authority and canonical-copy workflow arrangement. The open-work index correctly distinguishes proposals from implemented behavior. Completed specs are already deleted from the active directory. [ADR][s-adr] [Open work][s-open]

**Delete/simplify.** Revise the ADR to preserve product outcomes and trust boundaries, not particular release files or private helper ownership. Do not claim a new complexity-budget gate is needed: the ADR already explicitly avoids one.

Keep one architecture page, one installation page per platform, one configuration reference, one operations/troubleshooting location, and a compact release checklist. Consolidate overlap; remove obsolete operational detail with its subsystem. Historical releases and useful accepted decisions remain in Git history/tags rather than becoming mandatory agent context.

Prioritize the confidence failure by S05 rather than growing compatibility around it. Pause a separate unanchored-advisory artifact family and broad new policy/exclusion machinery during simplification. Retain concise dissent through S11 without building a provenance subproduct. Do not dismiss the open trusted-image issue as unnecessary security; document the actual boundary and address it separately with a threat-model-driven change.

**Gain.** Smaller agent context and less chance that unfinished proposals reintroduce deleted machinery.

**Risk.** Less detailed historical context close to the code. Preserve concise explanations for surprising, still-current adapter/security behavior rather than deleting comments indiscriminately.

**Accept when.** The current product can be understood from a short set of authoritative documents, and plans cannot be mistaken for shipped behavior.

### X01 - Optional later: critique groups once, not individual findings that are regrouped afterward

**Direction.** Group independent review findings first. Give each critic one compact grouped concern with its source evidence, and accept one verdict per group. Remove critique-driven duplicate links and much of the post-group verdict arbitration.

**Potential gain.** Removes a feedback loop between grouping and critique, repeated per-source opinions, duplicate-link validation, and several collapse cases.

**Risk.** High quality risk: current third-party duplicate judgments can recover relationships the deterministic matcher misses. Early grouping can merge distinct issues or hide a weak variant from critique. Replacing the whole process with a model judge would also violate deterministic authority.

**Decision criterion.** Prototype on saved real findings and adjudicated fixtures. Compare false merges, missed corroboration, dissent visibility, and duplicate public comments. Adopt only if the lost capability is acceptable. Do not make this speculative experiment a prerequisite for S01-S19.

## 7. Guardrails that should survive the purge

The security model trusts pinned reviewer CLIs to implement their documented behavior; it is not a proof against a fully compromised CLI or hostile runner. Keep that limitation explicit. Input labels and SHA hashes do not create isolation or authenticity. [ADR][s-adr] [Input preparation][s-input] [Adapter process][s-process]

| Keep | Why it still pays for itself | Simplify how |
|---|---|---|
| Pinned reviewed revision and current-head recheck | Prevent reviewing/posting against different changes | Carry one revision context; keep the final posting check |
| Trusted runtime and source/rules separation | Reviewed code must not become executable privileged reviewer configuration | One sanctioned runtime path; no fallback into checkout tooling |
| Per-seat credential allowlists | A reviewer does not need platform write credentials or another provider's key | Build one explicit environment once |
| Fork/trigger trust checks | Secret availability is a CI boundary, not a parser property | Fewer supported modes; reject unsafe modes outright |
| Snapshot/path/link containment | Repository-controlled paths must not escape the snapshot | Keep the current safe mechanism until an actually simpler safe replacement is proven |
| Model/file boundary validation and run/seat binding | Malformed or stale output cannot become consensus evidence | Validate at boundaries, not after every trusted call |
| Unique-identity voting | Duplicate messages and self-critique must not manufacture consensus | One supporter set |
| Bot-author ownership of notes/threads | A checksum is not authorization | One ownership check before trusting/mutating state |
| Timeout, process-group cleanup, and output limits | A hung CLI or descendant must not hang CI indefinitely | One lifecycle implementation |
| Serialized publication and recoverable partial failure | APIs are not one atomic transaction | Lean ledger plus authenticated idempotent reconciliation |
| Safe rendering and secret redaction | Model output is untrusted text, not trusted markup/log content | One renderer and one redaction boundary |

Do not replace the safe snapshot walker with `copytree` or unsafe archive extraction merely to make the file shorter. A separate immutable-Git-object snapshot design could narrow the problem, but it is not validated by this review and is not part of the default plan.

## 8. Test disposition map

This map tells agents where to look; it is not a blanket file-deletion command. Some named files were inventoried rather than read in full.

| Test area / files | Recommended disposition |
|---|---|
| `test_release_finalize.py`, `test_release_tools.py`, `test_canary_evidence_records.py` | Delete assertions whose release protocol disappears; retain source/digest authorization and publication smoke coverage in the simpler release path |
| `test_docs_contract.py`, `test_check_markdown_links.py` | Delete formatting/parser/inventory meta-tests; extract any real executable install checks |
| `test_types_schema_alignment.py` | Delete the generic reflection checker after contract ownership is simplified |
| `test_packaged_smoke_contract.py` | Remove test-ID manifest policing; keep missing-binary/resource and zero-probe failure cases |
| `test_makefile_quality.py`, static portions of `test_ci_template.py` | Stop locking exact command spellings/layout; retain real trigger, credentials, DAG/failure behavior checks |
| `test_mock_reviewer.py` and mock/fallback sections of adapter tests | Move useful deterministic fixtures to the test-only harness; delete production-mode combinatorics |
| `test_adapter_runner.py`, `test_openrouter_adapters.py`, `test_opencode_client.py` | Retain current transport/argv/env failures; delete obsolete formats and retired paths only with evidence |
| `test_consensus_reducer.py`, `test_consensus_integrity.py`, `test_grouping.py`, golden fixtures | Keep core invariants; intentionally rewrite policy goldens for S11, not to conceal unintended changes |
| `test_state_plan.py`, `test_state_planning.py`, `test_consensus_state_matching.py` | Delete absence-resolution and retired heuristic assertions with their features; add explicit migration and human-disposition expectations |
| Publish/revision lifecycle integration tests and platform contract tests | Keep compact end-to-end coverage on both platforms |
| `test_verify_pipeline_trust.py`, state-note authenticity, redaction, hostile rendering, input-bundle security cases | Keep the actual trust-boundary assertions; relocate only when the boundary moves |
| `test_import_boundaries.py` | Retain a narrow purity/trust-boundary assertion if useful; do not preserve a frozen private module graph |

### Minimal behavioral regression portfolio

Use existing tests where they already express these outcomes; do not build a new generalized test framework.

1. Two unique supporters report a concern; one identity repeated does not; self-critique does not add support. Disagreement remains visible under the new policy.
2. Valid empty review differs from malformed/all-invalid/partial/missing/timeout output. Failed review plus valid independent critique remains usable critique evidence.
3. Grouping is order-stable and does not merge a transitive chain into one issue accidentally.
4. Changed head, wrong run, wrong seat, unknown critique target, and malformed artifacts cannot produce unauthorized publication.
5. Reviewer processes cannot access posting credentials, load repository-supplied agent configuration, or escape the intended snapshot through links/paths.
6. A hung descendant process is terminated within the deadline; debug handling remains bounded and redacted.
7. Re-running a review is idempotent; shifted anchors and human dispositions survive; uncertainty does not mutate the wrong thread; absence never auto-resolves after S13.
8. A partial platform failure or interrupted state write is reported and can be reconciled without silently losing human decisions.
9. Corroborated unplaceable findings remain visible in fallback output on both platforms.
10. Built images contain the resources and supported CLIs their runtime needs; the exact promoted digests were tested.

## 9. Implementation sequence

### Phase A - Retire peripheral obligations

Implement S02, S17, S18's low-risk tooling cuts, and S19's authority cleanup. Design and land S01 in a separate release-tooling change; retire its tests and docs alongside the implementation. Preserve current runtime behavior during this phase.

**Exit:** ordinary developer checks are simple; packaged checks still prove packaging; a trial release under the smaller process succeeds.

### Phase B - Remove alternate runtime modes and duplicate facts

Implement S03. Then S04 and S06 together as coordinated producer/consumer contract changes. Apply S12 to the resulting smaller set of boundaries. Land S10 separately with differential tests so it does not become entangled with policy changes.

**Exit:** production has one real execution path, one resolved configuration, and one outcome per seat/stage. Offline fixture testing still works.

### Phase C - Simplify adapters and model contracts

Implement S05 first, so parser/launcher refactoring targets the smaller contract. Then S07-S09, adapter by adapter, deleting the old path as each replacement is accepted. Preserve a captured current transport corpus and run changed adapter integration checks.

**Exit:** all four adapters work with the smaller schema and explicit codecs; no universal fallback parser or cross-language production scaffolding is required.

### Phase D - Make the product deliberately smaller

Implement S11, then S13-S15, with explicit new lifecycle/policy expectations and one-time state migration. Keep X01 out of this phase. Update the ADR and user-facing migration notes to state the removed conveniences clearly.

**Exit:** two-support informational policy, human-controlled closure, lean persistent identity, and simpler publication work on both platforms without losing human dispositions.

### Phase E - Collapse residual workflow/contract overhead

Implement S16 using the stabilized runtime contracts. Remove dead tests, configuration names, schema files, docs, and fallback imports left by previous phases. Do not leave disabled feature flags as a substitute for deletion.

**Exit:** no retired path is reachable; a maintainer can understand a normal review and a failed review without learning the old implementation.

### Coordination

Release/docs work can proceed independently of most runtime work. S04/S06/S12 share contract files and need one integration owner. S05/S07/S08/S09 share adapter execution and should be staged rather than independently rewritten by competing agents. S11/S13/S14/S15 share consensus/state/publication assumptions and need a single agreed transition plan.

Use small conceptual commits, not necessarily tiny line diffs. A deletion can legitimately remove many tests and documents in one coherent change. Do not keep both implementations indefinitely to reduce review anxiety.

## 10. Migration and completion criteria

Use the next appropriate breaking release for incompatible public configuration/artifact/state changes. Internal helper imports are not public compatibility obligations, but CI template/job names, documented commands, model schemas consumed by adapters, and persisted state can be. Inventory actual consumers before deleting an externally used contract. [ADR][s-adr] [Release process][s-release]

Do not mutate old tags/releases. Provide a compact old-to-new configuration mapping and a one-time state export/migration tool where needed. Do not create a permanent migration framework or a second compatibility database. Unsupported old input should fail with a useful instruction, not be silently ignored or endlessly interpreted.

For each work item, agents should report:

- Which user-visible behavior was intentionally removed and what remains.
- Files/options/artifacts/tests deleted, and any replacement code introduced.
- The preserved behavioral contract and tests actually run.
- Live/packaged checks performed versus still unverified.
- Migration implications and unresolved risks.

Measure net implementation and configuration reduction after the work, not as an automated quota or reason to delete meaningful safety tests. The goal is less code needed to explain correct behavior, not fewer lines at any cost.

**Final acceptance:** the same four-agent, two-platform tribunal remains useful; its operators no longer maintain a release-governance product, production mock modes, repeatedly resolved configuration, model-authored hashes, a universal answer parser, automatic absence-resolution policy, and tests dedicated to all of those retired behaviors.

## Source links

These links are evidence anchors, not a claim that every linked file was read in full. The report distinguishes implementation observations, proposed changes, and experiments.

[s-adr]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/docs/decisions/0003-product-invariants-and-complexity-envelope.md
[s-config-yaml]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/config/review.yaml
[s-config]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/config.py
[s-policy]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/consensus_policy.py
[s-common]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/adapters/common.sh
[s-grouping]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/grouping.py
[s-release]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/docs/development/release-process.md
[s-make]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/Makefile
[s-doc-tests]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/tests/unit/test_docs_contract.py
[s-runner]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/adapter_runner.py
[s-process]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/adapter_process.py
[s-prompts]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/prompt_render.py
[s-consensus]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/consensus.py
[s-raw-schema]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/schemas/raw_finding_batch.schema.json
[s-schema]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/schema.py
[s-open]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/docs/improvement-specs/README.md
[s-opencode]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/adapters/opencode.sh
[s-output]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/adapter_output.py
[s-critique]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/critique.py
[s-type-tests]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/tests/unit/test_types_schema_alignment.py
[s-state-plan]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/state_plan.py
[s-posting]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/posting.py
[s-memory]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/memory.py
[s-compat]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/docs/development/temporary-compatibility.md
[s-gh-ci]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/ci/review.github-actions.yml
[s-gl-ci]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/ci/review.gitlab-ci.yml
[s-smoke]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review_smoke/loader.py
[s-tools]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/pyproject.toml
[s-input]: https://github.com/seanleecoder/code-tribunal/blob/ec7e82754db062797a9c4d0646ced79ac2d8e7ff/ai-review/src/ai_review/input_bundle.py
