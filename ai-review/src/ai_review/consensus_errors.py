"""The consensus integrity exception, alone and package-independent.

Its own module so that ``critique`` can raise it without importing
``consensus``. The class is defined partway down ``consensus.py``, so importing
it from there fails on a partially-initialized module rather than merely linting
badly — see SPEC-39 Part 3 step 0.

This module must contain no intra-package import whatsoever. Total package
independence is the property being protected, and
``tests/unit/test_import_boundaries.py`` asserts it directly.
"""

from __future__ import annotations


class ConsensusIntegrityError(ValueError):
    """Fatal cross-stage artifact or effective-config integrity failure."""
