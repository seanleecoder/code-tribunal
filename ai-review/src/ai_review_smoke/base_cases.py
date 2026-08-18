"""Base-tag packaged-runtime cases.

Everything here holds for ``AI_REVIEW_BASE_TAG``: the runtime files and packaged
fixtures a preflight resolves are present, every runtime module imports, the
shipped schemas and default config load, and the container works under
``--read-only --tmpfs /tmp``. Nothing here needs a pinned reviewer CLI, which is
why the seat-level checks live in :mod:`ai_review_smoke.reviewer_cases` instead
-- the base image does not have those CLIs, so a single suite run against one tag
could not cover both halves.
"""

from __future__ import annotations

import errno
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from .manifest import (
    CLI_MODULES,
    PACKAGED_FIXTURES,
    RUNTIME_FILES,
    RUNTIME_MODULES,
)
from .paths import packaged_root

# Set by ai-review/images/base.Dockerfile and by nothing else; documented in
# docs/configuration.md as carrying no production runtime behavior. It is the one
# signal that distinguishes "running inside the published image" from "running in
# a clone", which two cases below need in order to assert image-only properties
# without asserting them falsely elsewhere.
_PACKAGED_RUNTIME_MARKER = "AI_REVIEW_PACKAGED_RUNTIME"


class PackagedBaseImageTests(unittest.TestCase):
    def test_expected_runtime_files_exist(self) -> None:
        root = packaged_root()
        for relative in RUNTIME_FILES:
            with self.subTest(path=relative):
                self.assertTrue(
                    (root / relative).is_file(),
                    f"{relative} is missing from the packaged runtime at {root}",
                )

    def test_packaged_fixtures_exist_where_the_reviewer_preflight_reads_them(self) -> None:
        """The reviewer preflight resolves ``--diff``/``--repo`` from these paths.

        It runs ``docker run --read-only`` with no mount, so a fixture missing from
        the image breaks it there rather than here. Assert the exact paths in the
        image's own tree, which is what the base preflight used to do as inline
        ``test -f`` / ``test -d`` shell.
        """
        root = packaged_root()
        for relative, kind in PACKAGED_FIXTURES:
            with self.subTest(path=relative):
                target = root / relative
                if kind == "file":
                    self.assertTrue(target.is_file(), f"{relative} must ship as a file")
                else:
                    self.assertTrue(target.is_dir(), f"{relative} must ship as a directory")

    def test_every_runtime_module_imports(self) -> None:
        for module_name in RUNTIME_MODULES:
            with self.subTest(module=module_name):
                importlib.import_module(module_name)

    def test_packaged_cli_entry_points_are_callable(self) -> None:
        """Import alone does not prove ``python -m`` still has an entry point."""
        for module_name in CLI_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                entry_point = getattr(module, "main", None) or getattr(module, "cli", None)
                self.assertTrue(
                    callable(entry_point),
                    f"{module_name} must expose a callable main() or cli()",
                )

    def test_shipped_schemas_load(self) -> None:
        import jsonschema  # type: ignore[import-untyped]
        from ai_review.schema import load_schema, schema_dir

        schemas = sorted(path.name for path in schema_dir().glob("*.json"))
        self.assertTrue(schemas, f"no schemas shipped in {schema_dir()}")
        for name in schemas:
            with self.subTest(schema=name):
                schema = load_schema(name)
                jsonschema.Draft202012Validator.check_schema(schema)

    def test_default_config_loads(self) -> None:
        """The document a consumer pipeline gets when it sets no ``AI_REVIEW_CONFIG``."""
        from ai_review.config import load_config

        config = load_config(packaged_root() / "config" / "review.yaml")

        self.assertEqual(config["schema_version"], "review_config.v3")
        self.assertTrue(config["reviewers"])

    def test_tmp_is_writable_for_adapter_scratch_space(self) -> None:
        """Every stage needs a writable temp mount: prompts, snapshots, scratch dirs.

        The preflight runs the container as ``--read-only --tmpfs /tmp``, so this is
        the half of that arrangement the runtime depends on.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
            handle.write(json.dumps({"tmpfs": "writable"}))
            handle.flush()
            self.assertTrue(Path(handle.name).is_file())
            scratch = Path(handle.name).parent / "ai-review-smoke-scratch"
            self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
            (scratch / "nested").mkdir(parents=True)
            self.assertTrue((scratch / "nested").is_dir())

    def test_packaged_runtime_root_is_read_only(self) -> None:
        """The other half: nothing in a review run may write into the image tree.

        Adapters write only under the output dir and the temp dir, so a layer that
        left ``/opt/ai-review`` writable, or a preflight that dropped
        ``--read-only``, changes what the published image is. Guarded on the
        packaging marker ``base.Dockerfile`` sets, which is always present in the
        image and never in a clone -- so this skips only where there is no packaged
        root to assert, and the manifest still requires the case to exist.
        """
        if os.environ.get(_PACKAGED_RUNTIME_MARKER) != "1":
            self.skipTest(f"{_PACKAGED_RUNTIME_MARKER} is unset; not a packaged runtime")

        blocked = packaged_root() / "smoke-should-not-be-writable"
        with self.assertRaises(OSError) as caught:
            blocked.write_text("", encoding="utf-8")
        self.assertEqual(
            caught.exception.errno,
            errno.EROFS,
            f"expected a read-only filesystem at {packaged_root()}",
        )
