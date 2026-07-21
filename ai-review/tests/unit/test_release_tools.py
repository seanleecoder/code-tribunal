from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
REQUIRED_RELEASE_SCRIPTS = (
    "build_release_manifest.py",
    "check_release_inputs.py",
    "check_release_manifest.py",
    "release_common.py",
)
if not all((SCRIPTS / name).is_file() for name in REQUIRED_RELEASE_SCRIPTS):
    raise unittest.SkipTest("repository-only release tooling is absent from the runtime image")
sys.path.insert(0, str(SCRIPTS))

from build_release_manifest import build_manifest  # noqa: E402
from check_release_inputs import validate_release_inputs  # noqa: E402
from check_release_manifest import validate_manifest  # noqa: E402
from release_common import (  # noqa: E402
    HASH_GROUPS,
    ReleaseValidationError,
    aggregate_hash,
    canonical_json_bytes,
    computed_hashes,
    disallowed_release_paths,
    sha256_bytes,
)


class ReleaseToolTests(unittest.TestCase):
    def _tree(self, destination: Path) -> None:
        paths = {item for group in HASH_GROUPS.values() for item in group}
        paths.update(
            {
                ".github/workflows/ai-review.yml",
                "ai-review/ci/review.github-actions.yml",
                "ai-review/ci/review.gitlab-ci.yml",
            }
        )
        for relative in paths:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, target)

    def _draft(self, root: Path) -> dict[str, object]:
        data = json.loads(
            (REPO_ROOT / "release/release-inputs.json").read_text(encoding="utf-8")
        )
        data["hashes"] = computed_hashes(root)
        return data

    def _active(self, root: Path) -> dict[str, object]:
        runtime_source = "a" * 40
        base_digest = "sha256:" + "b" * 64
        reviewer_digest = "sha256:" + "c" * 64
        base_name = "ghcr.io/example/code-tribunal/ai-review-base"
        reviewer_name = "ghcr.io/example/code-tribunal/ai-review-reviewer"
        expected = {
            "base": f"{base_name}:1.0-{runtime_source}@{base_digest}",
            "reviewer": f"{reviewer_name}:1.0-{runtime_source}@{reviewer_digest}",
        }
        for relative in (
            ".github/workflows/ai-review.yml",
            "ai-review/ci/review.github-actions.yml",
        ):
            path = root / relative
            text = path.read_text(encoding="utf-8")
            old_refs = re.findall(r"^\s+container:\s+(\S+)\s*$", text, re.M)
            old_base = next(item for item in old_refs if "/ai-review-base:" in item)
            old_reviewer = next(item for item in old_refs if "/ai-review-reviewer:" in item)
            path.write_text(
                text.replace(old_base, expected["base"]).replace(
                    old_reviewer, expected["reviewer"]
                ),
                encoding="utf-8",
            )
        gitlab = root / "ai-review/ci/review.gitlab-ci.yml"
        text = gitlab.read_text(encoding="utf-8")
        text = re.sub(
            r'AI_REVIEW_BASE_IMAGE: "[^"]+"',
            f'AI_REVIEW_BASE_IMAGE: "{expected["base"]}"',
            text,
            count=1,
        )
        text = re.sub(
            r'AI_REVIEW_REVIEWER_IMAGE: "[^"]+"',
            f'AI_REVIEW_REVIEWER_IMAGE: "{expected["reviewer"]}"',
            text,
            count=1,
        )
        text = re.sub(
            r'AI_REVIEW_TRUSTED_IMAGE_SHA: "[0-9a-f]+"',
            f'AI_REVIEW_TRUSTED_IMAGE_SHA: "{runtime_source}"',
            text,
            count=1,
        )
        gitlab.write_text(text, encoding="utf-8")

        data = self._draft(root)
        data["status"] = "active"
        data["runtime_source"] = runtime_source
        data["images"] = {
            "base": {"name": base_name, "digest": base_digest},
            "reviewer": {"name": reviewer_name, "digest": reviewer_digest},
        }
        data["verification"] = {
            "ci_run_id": "github-ci-123",
            "publication_run_id": "github-images-456",
            "evidence_record_ids": ["gitlab-boundary-A", "github-lifecycle-A"],
        }
        return data

    def test_draft_current_tree_is_valid(self) -> None:
        data = json.loads(
            (REPO_ROOT / "release/release-inputs.json").read_text(encoding="utf-8")
        )
        validate_release_inputs(data, REPO_ROOT)

    def test_active_happy_path_matches_every_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            validate_release_inputs(self._active(root), root)

    def test_mismatched_github_pin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            data = self._active(root)
            workflow = root / "ai-review/ci/review.github-actions.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "@sha256:" + "b" * 64,
                    "@sha256:" + "d" * 64,
                    1,
                ),
                encoding="utf-8",
            )
            data["hashes"] = computed_hashes(root)
            with self.assertRaisesRegex(ReleaseValidationError, "workflow copies differ"):
                validate_release_inputs(data, root)

    def test_malformed_runtime_sha_and_digest_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            for field, value, message in (
                ("runtime_source", "ABC123", "runtime_source"),
                ("base_digest", "sha256:1234", "lowercase sha256 digest"),
            ):
                with self.subTest(field=field):
                    data = self._active(root)
                    if field == "runtime_source":
                        data["runtime_source"] = value
                    else:
                        data["images"]["base"]["digest"] = value
                    with self.assertRaisesRegex(ReleaseValidationError, message):
                        validate_release_inputs(data, root)

    def test_config_schema_and_lock_drift_are_rejected(self) -> None:
        for relative in (
            "ai-review/config/review.yaml",
            "ai-review/schemas/state.schema.json",
            "ai-review/images/package-lock.json",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._tree(root)
                data = self._draft(root)
                path = root / relative
                path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaisesRegex(ReleaseValidationError, "hashes are stale"):
                    validate_release_inputs(data, root)

    def test_placeholder_is_rejected_even_in_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            data = self._draft(root)
            data["verification"]["ci_run_id"] = "TBD"
            with self.assertRaisesRegex(ReleaseValidationError, "placeholder"):
                validate_release_inputs(data, root)

    def test_aggregate_hash_is_order_independent_and_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a").write_text("first", encoding="utf-8")
            (root / "b").write_text("second", encoding="utf-8")
            original = aggregate_hash(root, ["b", "a"])
            self.assertEqual(original, aggregate_hash(root, ["a", "b"]))
            (root / "a").write_text("changed", encoding="utf-8")
            self.assertNotEqual(original, aggregate_hash(root, ["a", "b"]))

    def test_release_path_allowlist_is_path_scoped(self) -> None:
        paths = [
            "release/1.0.0.md",
            "docs/history/evidence/github.md",
            "ai-review/src/ai_review/config.py",
        ]
        self.assertEqual(
            disallowed_release_paths(paths), ["ai-review/src/ai_review/config.py"]
        )

    def test_canonical_json_has_deterministic_key_order(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"z": 1, "a": {"d": 2, "b": 1}}),
            b'{\n  "a": {\n    "b": 1,\n    "d": 2\n  },\n  "z": 1\n}\n',
        )

    def test_manifest_generator_and_validator_clean_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            inputs = self._active(root)
            release_inputs = root / "release/release-inputs.json"
            release_inputs.parent.mkdir(parents=True)
            release_inputs.write_bytes(canonical_json_bytes(inputs))
            runtime_source = inputs["runtime_source"]
            release_commit = "d" * 40
            changed_paths = ["CHANGELOG.md", "release/1.0.0.md"]
            with mock.patch(
                "build_release_manifest.git_changed_paths", return_value=changed_paths
            ):
                manifest = build_manifest(
                    "v1.0.0",
                    runtime_source,
                    release_commit,
                    release_inputs,
                    root,
                )

            self.assertEqual(manifest["changed_paths"], changed_paths)
            self.assertEqual(
                manifest["release_inputs_sha256"], sha256_bytes(release_inputs.read_bytes())
            )
            with mock.patch(
                "check_release_manifest.git_changed_paths", return_value=changed_paths
            ):
                validate_manifest(manifest, release_inputs, root)

    def test_manifest_generator_rejects_disallowed_runtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            inputs = self._active(root)
            release_inputs = root / "release/release-inputs.json"
            release_inputs.parent.mkdir(parents=True)
            release_inputs.write_bytes(canonical_json_bytes(inputs))
            with (
                mock.patch(
                    "build_release_manifest.git_changed_paths",
                    return_value=["ai-review/src/ai_review/config.py"],
                ),
                self.assertRaisesRegex(ReleaseValidationError, "disallowed paths"),
            ):
                build_manifest(
                    "v1.0.0",
                    inputs["runtime_source"],
                    "d" * 40,
                    release_inputs,
                    root,
                )


if __name__ == "__main__":
    unittest.main()
