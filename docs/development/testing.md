# Testing strategy

Tests are organized by the boundary they protect:

- `unit/` pins parsing, configuration, adapters, consensus, posting, and
  workflow contracts.
- `contract/` keeps platform implementations and golden consensus cases aligned.
- `integration/` exercises prepare and publish behavior across fake platforms
  (`test_publish_e2e.py`), and the cross-revision thread lifecycle
  (`test_revision_lifecycle_e2e.py`).
- `security/` covers hostile model text and state authenticity.

Repository CI runs `make quality`. Live platform checks are recorded separately
because local fakes cannot prove protected-variable behavior, platform token
scope, required-check configuration, or real container registry pulls. See the
[evidence runbook](../evidence/README.md).

When behavior changes, update the smallest relevant test and any schema-backed
golden file. Run `make update-golden` only for an intentional reducer contract
change and review the generated diff.

`make quality` checks concrete backticked repository paths and test references in
current Markdown. It reports missing targets with file and line, and checks test
definitions parsed from the tests tree. Fenced examples, placeholder paths, and
globs are patterns; tagged release notes, archive documents, and CHANGELOG history
are outside this existence check. Proposed improvement specs are exempt from
existence checks but retain their Markdown link checks and other contracts.
When current prose deliberately names a deleted target, put
`<!-- docs-check: historical -->` on that same line. Documented defaults, schema
versions, and job names still require review; existence cannot prove behavior.
