"""Resolve the packaged runtime root from this module's own location.

The suite ships at ``/opt/ai-review/src/ai_review_smoke`` and every path it
asserts is relative to ``/opt/ai-review``, so the root is derived rather than
configured: a preflight cannot point the suite at a checkout and have it assert
the checkout's files instead of the image's. The same derivation resolves to
``ai-review/`` in a clone, which is what lets the checkout contract test verify
this suite's manifests against the real package.
"""

from __future__ import annotations

from pathlib import Path


def packaged_root() -> Path:
    """The directory holding ``adapters/``, ``config/``, ``schemas/``, ``src/``."""
    return Path(__file__).resolve().parents[2]
