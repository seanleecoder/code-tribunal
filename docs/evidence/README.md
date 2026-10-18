# Live evidence

Live evidence complements executable tests; it does not replace them. Record only
sanitized identifiers, digests, expected and actual outcomes, and audit results.
Never store credentials, CLI session material, private consumer coordinates,
proprietary source, or sensitive model content.

| Page | Contents |
|---|---|
| [`RUNBOOK.md`](RUNBOOK.md) | operator mechanics for live checks, manual diagnosis of canary campaigns, and the [carried coverage gaps](RUNBOOK.md#carried-coverage-gaps) |
| [`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md) | the public demo consumers the canary and manual checks use, and their per-release setup |

## Per-release evidence

From 2.1.0, a release's evidence is its signed tag, the image digests its canonical
templates pin, their provenance attestations, and the cited Candidate Canary run,
whose verdicts the release body records; see the
[release process](../development/release-process.md). Known limitations are written
in that version's CHANGELOG section.

Releases up to 2.0.3 used release inputs, generated evidence records and waivers.
Their notes are under [`release/`](../../release/), and their records stay at each
tag, for example
[`v2.0.3:docs/evidence/`](https://github.com/seanleecoder/code-tribunal/tree/v2.0.3/docs/evidence),
which also holds the 1.0.0 historical evidence matrix.

## Live versus regression coverage

Live runs spend real tokens and platform quota, so they cover only what a test
cannot stand in for.

| Behavior | Proven by |
|---|---|
| Image identity, OCI revision labels, provenance | canary `verify-candidate` |
| Real review and critique on every seat, both platforms | canary `panel` |
| Posting and state lifecycle on the real platform APIs | canary `lifecycle` (mock reviewers); `test_post.py`, `integration/test_publish_e2e.py`, `integration/test_revision_lifecycle_e2e.py` |
| Protected-credential withholding and composition audit on GitLab | canary `hostile`; `test_verify_pipeline_trust.py`, fork-secret withholding in `test_input_bundle.py` |
| Snapshot symlink containment (SPEC-31) | `test_input_bundle.py`; at most one variant live |
| Artifact and configuration integrity (SPEC-33) | `test_consensus_integrity.py` |
| GitHub revision races and the HTTP 406 path (SPEC-34) | `test_input_bundle.py`, `test_github_platform.py`; the windows are too narrow to reproduce live, except the stale-head no-op the canary drives |
| Below-quorum FYI and the inline-unmappable summary fallback | `integration/test_publish_e2e.py`, `test_post.py` |
| Finding identity across line movement | `integration/test_revision_lifecycle_e2e.py`, `test_anchors.py` |
