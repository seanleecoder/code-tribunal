# Testing strategy

Tests are organized by the boundary they protect:

- `unit/` pins parsing, configuration, adapters, consensus, posting, and
  workflow contracts.
- `contract/` keeps platform implementations and golden consensus cases aligned.
- `integration/` exercises prepare and publish behavior across fake platforms
  (`test_publish_e2e.py`), and the cross-revision thread lifecycle
  (`test_revision_lifecycle_e2e.py`).
- `security/` covers hostile model text and state authenticity.

Repository CI runs `make quality`. Live platform checks run in the Candidate
Canary before each release, because local fakes cannot prove protected-variable
behavior, platform token scope, required-check configuration, or real container
registry pulls. See the [release process](release-process.md).

When behavior changes, update the smallest relevant test and any schema-backed
golden file. Run `make update-golden` only for an intentional reducer contract
change and review the generated diff.

Test artifact producers and admission boundaries against the shipped JSON
schemas, with concrete artifacts; see [module ownership](architecture.md#module-ownership).

`make test` invokes pytest directly with coverage. `make docs-check` uses the
pinned native Lychee binary to check local Markdown links and anchors. The
historical `release/<version>.md` notes are frozen and not link-checked. GitLab
installation examples are validated by the pipeline-trust tests. Release tooling
is covered by `test_check_release.py`, which builds throwaway repositories and a
fixture signing key. IDs in the temporary-compatibility register are checked
against their code markers by `test_temporary_compatibility.py`.

## Temporary Codex sandbox investigation

COMPAT-012 adds a four-way diagnostic after a failed Codex image gate, on the
same Ubuntu runner and resolved Linux/amd64 image ID. To run it on a Linux Docker
host with an already-built reviewer image:

```bash
python3 scripts/diagnose_codex_sandbox.py <reviewer-image> /tmp/codex-sandbox-evidence
```

The evidence directory must be new. The default, seccomp-only, AppArmor-only and
combined profile experiments use no provider secrets and no external network.
Each container has a 180-second bound and explicit removal on timeout; the CI
step has a 16-minute bound. Setup/transport failures remain distinct from a
reproduced namespace denial. A missing or successful baseline, four denied reads,
or incomplete evidence makes the diagnostic fail. The original image gate stays
failed regardless of diagnostic results.

Only the read case using the real Sol model ID runs in this diagnostic subset.
The ordinary gate collects the previous 13 cases plus a read-only enforcement
case under its existing 900-second wrapper bound. The latter requires a random
snapshot sentinel read, denied mutations/deletions and protected runtime/input
writes, and unchanged bytes observed by the trusted parent after CLI exit and
before snapshot cleanup. A namespace failure cannot satisfy that check.

Runtime evidence records kernel/runner/Docker identity, process seccomp and
capability status, AppArmor context, user-namespace sysctls, available audit data,
and system/bundled bwrap paths, hashes and help/version output. Unavailable
observations are recorded explicitly. The workflow uploads the detailed evidence
as `codex-sandbox-diagnostic`, retained for seven days. Console output contains
case classifications and the summary. Termux unit tests validate collection and
failure admission, not Linux enforcement or deployment compatibility.

A successful relaxation identifies a candidate restriction to investigate with
audit evidence or a targeted probe. It does not select a production policy.
Remove COMPAT-012 after collecting evidence. Production deployment changes,
GitHub/GitLab compatibility claims and release validation require the subsequent
policy decision and live checks described in the agreed PR #304 plan.

Configuration tables, headings, ordinary prose, and backticked code/test names
are reviewed by people rather than a documentation-policy checker. Review
changes to documented defaults, schema versions, and job names against their
[authorities](../decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
