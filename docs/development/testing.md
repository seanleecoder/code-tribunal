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

`make test` invokes pytest directly with coverage. `make docs-check` uses the
pinned native Lychee binary to check local Markdown links and anchors. The
historical `release/<version>.md` notes are frozen and not link-checked. GitLab
installation examples are validated by the pipeline-trust tests. Release tooling
is covered by `test_check_release.py`, which builds throwaway repositories and a
fixture signing key. IDs in the temporary-compatibility register are checked
against their code markers by `test_temporary_compatibility.py`.

Configuration tables, headings, ordinary prose, and backticked code/test names
are reviewed by people rather than a documentation-policy checker. Review
changes to documented defaults, schema versions, and job names against their
[authorities](../decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
