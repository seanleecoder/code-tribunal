# SPEC-28 — Packaging metadata: drop unused python-gitlab, correct description

- **Severity:** Medium (supply-chain surface + misleading metadata) · **Effort:** XS · **ROI rank:** 6 (pre-1.0)
- **Depends on:** none.

## Why

- `python-gitlab>=4.7` is declared in `pyproject.toml` and pinned into the
  runtime image (`python-constraints.txt`, `base.Dockerfile`), but is **never
  imported anywhere** — the code ships its own requests-based
  `gitlab_client.py`. Dead dependency = unnecessary install weight and
  supply-chain surface in the trusted image.
- The package description says "for GitLab merge requests" although GitHub
  pull requests are a headline, dogfooded feature.
- (Release tags v0.1.0–v0.4.0 exist on the remote — an earlier review claim
  that tags were missing was a shallow-fetch artifact; no action on tags.)

## Scope

**In:** dependency removal across the four pin locations; description fix;
CHANGELOG entries. **Out:** version bump to 1.0.0 (happens in the release PR
after SPEC-23…29 land; `test_version_metadata.py` enforces
pyproject/`__init__`/CHANGELOG agreement then).

## Implementation

1. Remove `python-gitlab` from:
   - `pyproject.toml` `[project].dependencies`;
   - `ai-review/images/python-constraints.txt` — re-resolve the constraints
     from a clean resolver; drop transitive-only leftovers that no remaining
     direct dependency needs (e.g. `requests-toolbelt` is python-gitlab's
     dependency — confirm via the resolver before deleting);
   - `scripts/check_supply_chain_pins.py:PYTHON_DIRECT_PACKAGES`;
   - `ai-review/images/base.Dockerfile` pip install line.
2. `pyproject.toml` description → "CI-native multi-agent consensus code
   review for GitLab merge requests and GitHub pull requests".
3. `CHANGELOG.md` Unreleased: Removed (python-gitlab dependency) + Changed
   (description) entries.

## Acceptance criteria

- Full test suite, `make lint`, and `python scripts/check_supply_chain_pins.py`
  green without python-gitlab anywhere.
- `docker build -f ai-review/images/base.Dockerfile .` succeeds (its embedded
  unittest run passes) with the slimmed constraint set.

## Tests

- Existing `test_check_supply_chain_pins.py` (update the direct-package
  fixture expectations).
- Grep gate in review: no `import gitlab` / `python_gitlab` references exist
  (none today; keep it that way).

## Risk / rollback

None meaningful — the dependency is unreferenced. Rollback = revert.
