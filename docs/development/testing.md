# Testing strategy

Tests are organized by the boundary they protect:

- `unit/` pins parsing, configuration, adapters, consensus, posting, gate, and
  workflow contracts.
- `contract/` keeps platform implementations and golden consensus cases aligned.
- `integration/` exercises prepare/post/gate behavior across fake platforms.
- `security/` covers hostile model text and state authenticity.

Repository CI runs `make quality`. Live platform checks are recorded separately
because local fakes cannot prove protected-variable behavior, platform token
scope, required-check configuration, or real container registry pulls. See the
[evidence runbook](../history/evidence/README.md).

When behavior changes, update the smallest relevant test and any schema-backed
golden file. Run `make update-golden` only for an intentional reducer contract
change and review the generated diff.
