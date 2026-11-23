"""Captured Codex exec --json runs and the codec call that admits them.

The output and stream-filter suites both admit the same fixtures, so the
fixture path and the codec call live here and neither suite owns them.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_review.adapter_output import _load_adapter_json

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"


def capture(label: str) -> dict:
    return json.loads((FIXTURES / f"{label}.json").read_text())


def load(data: dict, stage: str = "review") -> dict:
    return _load_adapter_json(
        data["stdout"],
        returncode=data["returncode"],
        codec="codex_stream_json",
        stage=stage,
    )
