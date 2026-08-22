"""Packaged-runtime smoke suite for the published AI review images.

This package is the one deliberate exception to "runtime images carry no test
code" (SPEC-58). It exists because the property the removed executed-test floor
was compensating for is structural, not numeric: the preflight used to bind-mount
the checkout suite over ``/opt/ai-review/tests`` and run ``unittest discover``
against it, and both halves of that arrangement can pass while verifying nothing
-- ``docker run -v`` silently creates an empty directory when the host path is
missing or renamed, and ``unittest discover`` exits 0 on zero collection.

Shipping the suite restores the guarantee by construction:

* ``base.Dockerfile`` ``COPY``s this package, and ``COPY`` fails at build time on
  a missing path, so a renamed or deleted suite fails the build;
* the preflight invokes it **by module name** (``python -m ai_review_smoke``),
  not by discovery against a mount, so an absent suite raises
  ``ModuleNotFoundError`` and exits non-zero;
* :mod:`ai_review_smoke.manifest` declares the exact test IDs the suite must
  contain, :mod:`ai_review_smoke.loader` builds the ``unittest.TestSuite`` by
  naming those cases directly, and the run fails when the loaded ID set is not
  equal to the manifest.

The exception is bounded by three limits, which is what keeps it narrow enough
to be worth making:

* self-contained standard-library code -- no pytest, no network, and no
  execution surface the runtime does not already have;
* it never runs during the image build, only at preflight, so a change to smoke
  test code still does not alter image identity;
* the ``COPY`` names this package alone, so a revert to copying the whole test
  tree still fails ``test_container_ships_fixtures_and_only_the_packaged_smoke_suite``.

The checkout ``pytest`` suite remains the authoritative product test suite. This
suite asserts only properties of the *image*: that the runtime files and
fixtures a preflight resolves are present, that every runtime module imports,
that the shipped schemas and default config load, and -- on the reviewer tag --
that the pinned CLIs and every seat's local mock review, critique, and consensus
run work inside the container.
"""

from __future__ import annotations

__all__ = ["manifest"]
