from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_SCANNER = Path(__file__).resolve().parents[3] / "scripts" / "scan_evidence_leaks.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("scan_evidence_leaks", _SCANNER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load evidence scanner from {_SCANNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    _SCANNER.exists(),
    "repository-only evidence scanner is absent from the runtime image",
)
class EvidenceLeakScannerTests(unittest.TestCase):
    def test_detects_known_credential_shapes(self) -> None:
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "trace.log").write_text(
                "OPENROUTER_API_KEY=sk-or-v1-abcdef0123456789abcdef\n", encoding="utf-8"
            )
            (root / "other.log").write_text(
                "PRIVATE-TOKEN: glpat-ABCDEFGHIJKLMNOPQRST\n", encoding="utf-8"
            )
            findings, scanned, _ = scanner.scan([root])
        self.assertEqual(scanned, 2)
        self.assertTrue(findings, "expected credential detections")
        detectors = set(findings)
        self.assertTrue(
            {"openrouter-key", "gitlab-token", "private-token-header"} & detectors,
            f"unexpected detectors: {detectors}",
        )

    def test_legitimate_digests_are_not_flagged(self) -> None:
        """Evidence artifacts are full of 64-hex digests; they must stay quiet."""
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = {
                "context_hash": "a" * 64,
                "image_digest": "sha256:" + "f2a433ac1094d45943a2973c334ff0d711d6aca7" + "3" * 24,
                "run_id": "gh-30173073036-1",
                "runtime_source": "88bc9412b283d4a44328ab3ffd9f9708b0290f8e",
            }
            (root / "consensus.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
            findings, _, _ = scanner.scan([root])
        self.assertEqual(findings, {}, f"false positive on digest fields: {findings}")

    def test_opaque_high_entropy_token_is_flagged(self) -> None:
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "trace.log").write_text(
                "TOKEN=Zq7Xb2Lm9Rt4Vw8Np1Kd6Yh3Jc5Gs0Ae\n", encoding="utf-8"
            )
            findings, _, _ = scanner.scan([root])
        self.assertIn("opaque-high-entropy", findings)

    def test_exact_value_detection_reads_from_file(self) -> None:
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "trace.log").write_text("token=lowercasesecretvalue\n", encoding="utf-8")
            secrets = root / "secrets.txt"
            secrets.write_text("lowercasesecretvalue\nshort\n", encoding="utf-8")
            values = scanner.load_exact_values(secrets)
            self.assertEqual(values, ["lowercasesecretvalue"], "short values must be dropped")
            findings, _, _ = scanner.scan([root / "trace.log"], exact_values=values)
        self.assertIn("exact-value", findings)

    def test_clean_tree_exits_zero_and_missing_path_exits_two(self) -> None:
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "post_result.json").write_text('{"status": "success"}', encoding="utf-8")
            self.assertEqual(scanner.main([str(root)]), 0)
        self.assertEqual(scanner.main([str(Path(raw) / "gone")]), 2)

    def test_hit_exits_one(self) -> None:
        scanner = _load_scanner()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "trace.log").write_text("sk-ant-abcdef0123456789\n", encoding="utf-8")
            self.assertEqual(scanner.main([str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
