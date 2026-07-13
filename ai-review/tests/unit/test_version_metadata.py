from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

import ai_review

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"


class VersionMetadataTests(unittest.TestCase):
    def test_package_and_release_metadata_match(self) -> None:
        if not _PYPROJECT.exists() or not _CHANGELOG.exists():
            self.skipTest("repository release metadata is not copied into runtime images")
        project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
        package_version = str(project["version"])

        self.assertRegex(package_version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(ai_review.__version__, package_version)
        self.assertRegex(
            _CHANGELOG.read_text(encoding="utf-8"),
            rf"(?m)^## \[{re.escape(package_version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        )


if __name__ == "__main__":
    unittest.main()
