"""Refuse a Codex model whose catalog metadata does not select code mode.

The pinned Codex CLI chooses a model's tool surface from its bundled model
catalog. A model whose entry sets ``tool_mode`` to ``code_mode_only`` gets the
surface the image gate verifies: code-mode ``exec`` over the read-only
repository tools. Any other catalogued model gets direct tools instead, such as
``tool_search``, and an uncatalogued one runs on fallback metadata. The Codex
adapter runs this check before the review prompt is sent, so such a model never
sees that prompt.

Codex strips at most one leading ``namespace/`` from the configured model and
then uses the catalog entry whose slug is a prefix of the rest. This check is
deliberately stricter: the rest must equal a catalog slug, and every catalog
slug that is a prefix of it must be ``code_mode_only``. So an OpenRouter
``:variant`` suffix, which Codex would resolve to the base slug, is refused.
The codec's fallback-metadata check stays as a second, independent signal.

Standard library only: ``codex.sh`` runs it as
``python -P -m ai_review.codex_catalog MODEL CLI``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

CODE_MODE = "code_mode_only"
# Listing reads local data and takes well under a second.
_LIST_TIMEOUT_SECONDS = 60
# Codex lists the catalog its exec run will use: CODEX_HOME's models_cache.json
# when an authenticated session has refreshed one, else the copy bundled with
# the pinned CLI. A seat's CODEX_HOME is its own fresh state directory, which
# the review then runs with. No credential is passed to the listing.
_LIST_ENVIRONMENT = ("PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG", "LC_ALL")


def model_error(catalog: Any, model: str) -> str | None:
    """Return why ``model`` cannot run on the verified surface, or None if it can."""
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        return "Codex model catalog has no model list"
    slugs = {
        entry["slug"]: entry.get("tool_mode")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("slug"), str) and entry["slug"]
    }
    name = model.split("/", 1)[1] if "/" in model else model
    if name not in slugs:
        return (
            f"Codex model {model} is not in the pinned Codex CLI's model catalog; "
            "choose a catalogued code-mode model"
        )
    if any(name.startswith(slug) and mode != CODE_MODE for slug, mode in slugs.items()):
        return (
            f"Codex model {model} does not run in code mode in the pinned Codex CLI's "
            "catalog, so its tool surface is unverified; choose a catalogued code-mode model"
        )
    return None


def list_catalog(cli: str) -> Any:
    """Return the pinned CLI's bundled model catalog."""
    environment = {name: value for name in _LIST_ENVIRONMENT if (value := os.environ.get(name))}
    listed = subprocess.run(
        [cli, "debug", "models"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
        timeout=_LIST_TIMEOUT_SECONDS,
        check=False,
    )
    if listed.returncode:
        raise ValueError(f"listing the Codex model catalog exited {listed.returncode}")
    return json.loads(listed.stdout)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: codex_catalog MODEL CLI", file=sys.stderr)
        return 2
    model, cli = argv
    try:
        error = model_error(list_catalog(cli), model)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # None of these messages quotes the CLI's output.
        error = f"Codex model catalog could not be read: {exc}"
    if error is None:
        return 0
    print(error, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
