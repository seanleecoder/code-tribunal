from __future__ import annotations

import base64
import tempfile
import tracemalloc
import unittest

from ai_review.redact import redact_file_tail, redact_text

_LAST = "\nlast progress\n"
_CLIPPED = "...[truncated]...\n[REDACTED]" + _LAST


def _file_tail(diagnostic: str) -> str:
    with tempfile.TemporaryFile() as capture:
        capture.write(diagnostic.encode())
        capture.flush()
        return redact_file_tail(capture, limit=4000)


class RedactionTests(unittest.TestCase):
    def test_secret_patterns_are_redacted(self) -> None:
        text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz token: glpat-abcdef123456"
        redacted = redact_text(text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("glpat-abcdef123456", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_value_with_special_chars_is_fully_redacted(self) -> None:
        # Bug #5: the value class used to stop at the first out-of-class character,
        # leaking the tail after '!'. The whole non-whitespace token must be masked.
        text = "api_key=abcdefgh!SECRETTAIL1234567890 trailing"
        redacted = redact_text(text)
        self.assertNotIn("SECRETTAIL", redacted)
        self.assertNotIn("abcdefgh", redacted)
        self.assertEqual(redacted, "api_key=[REDACTED] trailing")

    def test_aws_access_key_is_redacted(self) -> None:
        redacted = redact_text("id AKIAIOSFODNN7EXAMPLE end")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_github_token_is_redacted(self) -> None:
        redacted = redact_text("t ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 end")
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_jwt_is_redacted(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4"
        redacted = redact_text(f"auth {jwt}")
        self.assertNotIn(jwt, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_bearer_token_is_redacted(self) -> None:
        redacted = redact_text("Authorization: Bearer sometokenvalue123")
        self.assertNotIn("sometokenvalue123", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_pem_private_key_block_is_redacted(self) -> None:
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIabcSECRETLINE\nmoresecret\n"
            "-----END RSA PRIVATE KEY-----"
        )
        redacted = redact_text(pem + "\nafter")
        self.assertNotIn("MIIabcSECRETLINE", redacted)
        self.assertNotIn("moresecret", redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIn("after", redacted)

    def test_incomplete_pem_private_key_is_redacted_through_eof(self) -> None:
        diagnostic = "before\n-----BEGIN PRIVATE KEY-----\npartial-private-key"
        for redact in (redact_text, _file_tail):
            with self.subTest(redact=redact.__name__):
                self.assertEqual(redact(diagnostic), "before\n[REDACTED]")

    def test_only_standalone_quoted_pem_header_prose_is_preserved(self) -> None:
        header = "-----BEGIN RSA PRIVATE KEY-----"
        for delimiter in ("`", "``"):
            for ending in ("", "\n", "\r\n"):
                prose = (
                    f"key.pem commits a {delimiter}{header}{delimiter} header; rotate it.{ending}"
                )
                with self.subTest(delimiter=delimiter, ending=ending):
                    self.assertEqual(redact_text(prose, preserve_pem_header_prose=True), prose)
                    # The prose exception is limited to rendering, not diagnostics.
                    self.assertNotIn(header, redact_text(prose))
                    self.assertNotIn(header, _file_tail(prose))

        for text in (
            f"{header} header; rotate it.",
            f"`{header}`` header; rotate it.",
            f"``{header}` header; rotate it.",
            f"`{header} key-material`",
            f"`{header}`\nkey-material",
            f"`{header}` header; example: {header} key-material",
            f"`{header}` MIIEpAIBAAKCAQEA",
            f"`{header}` MIIEpAIBAAKCAQEA ----END RSA PRIVATE KEY----",
            f"`{header}`\u2028key-material",
            f"`{header}`\x85key-material",
        ):
            with self.subTest(text=text):
                redacted = redact_text(text, preserve_pem_header_prose=True)
                self.assertNotIn(header, redacted)
                self.assertNotIn("key-material", redacted)
                self.assertIn("[REDACTED]", redacted)

    def test_quoted_pem_header_keeps_ordinary_prose_and_references(self) -> None:
        header = "-----BEGIN RSA PRIVATE KEY-----"
        for explanation in (
            "header committed in config/production/deploy.pem; rotate it.",
            "header in commit " + "a3ae3330" * 5 + "; rotate it.",
            "header consumed by loadPrivateKeyFromEnvironment; rotate it.",
            "header is committed so you should rotate the key.",
            "header documented in README.md; rotate it.",
            "header described by MOCK_PRIVATE_KEY in MACHINE.md; rotate it.",
        ):
            with self.subTest(explanation=explanation):
                prose = f"`{header}` {explanation}"
                self.assertEqual(redact_text(prose, preserve_pem_header_prose=True), prose)
                self.assertNotIn(header, redact_text(prose))

    def test_quoted_pem_header_masks_decorated_key_prefixes(self) -> None:
        # Only public format prefixes are needed: incomplete blocks must not need
        # a complete, parseable private key before they are masked.
        prefixes = {
            "RSA": b"\x30\x82\x04\xa4\x02\x01\x00\x02\x82\x01\x01",
            "EC": b"\x30\x77\x02\x01\x01\x04\x20",
            "": b"\x30\x2e\x02\x01\x00\x30\x05\x06\x03",
            "ENCRYPTED": b"\x30\x82\x02\xdd\x30\x57\x06\x09",
            "OPENSSH": b"openssh-key-v1\x00\x00\x00\x00\x04none",
        }
        for kind, prefix in prefixes.items():
            header = "-----BEGIN " + (kind + " " if kind else "") + "PRIVATE KEY-----"
            material = base64.b64encode(prefix).decode()
            for width in (1, 4, 12):
                for separator in (" ", ",", ".", "-", "` `", "\u200b", "\u00ad"):
                    chunks = [material[i:i + width] for i in range(0, len(material), width)]
                    text = f"`{header}` " + separator.join(chunks)
                    with self.subTest(kind=kind, width=width, separator=separator):
                        self.assertEqual(
                            redact_text(text, preserve_pem_header_prose=True), "`[REDACTED]"
                        )

        for metadata in ("Proc-Type: 4,ENCRYPTED", "DEK-Info: AES-128-CBC,0123456789ABCDEF"):
            text = f"`-----BEGIN RSA PRIVATE KEY-----` {metadata} a,b,c,d"
            with self.subTest(metadata=metadata):
                self.assertEqual(redact_text(text, preserve_pem_header_prose=True), "`[REDACTED]")

    def test_multiline_quoted_pem_rejection_has_bounded_peak_allocation(self) -> None:
        text = "`-----BEGIN RSA PRIVATE KEY-----` header.\n" + "ab\n" * 100_000
        tracemalloc.start()
        try:
            redacted = redact_text(text, preserve_pem_header_prose=True)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(redacted, "`[REDACTED]")
        # Includes input-sized regex/suffix copies, but not one object per line.
        self.assertLess(peak, 8 * len(text))

    def test_file_tail_masks_non_ascii_secret_values_before_clipping(self) -> None:
        for value in ("é" * 10000, "中" * 10000, "pässwörd" * 3000):
            with self.subTest(value=value[:8]):
                self.assertEqual(_file_tail("token=" + value + _LAST), _CLIPPED)

    def test_file_tail_recognizes_all_unicode_whitespace_before_clipping(self) -> None:
        whitespace = (chr(value) for value in range(0x110000) if chr(value).isspace())
        for character in whitespace:
            for prefix in (f"token{character}={character}", f"Bearer{character}"):
                with self.subTest(prefix=prefix):
                    diagnostic = prefix + "never-publish-this" * 2000 + _LAST
                    self.assertEqual(_file_tail(diagnostic), _CLIPPED)

    def test_file_tail_masks_bearer_values_with_unicode_case_folds_before_clipping(self) -> None:
        for character in ("İ", "ı", "ſ", "K"):
            with self.subTest(character=character):
                diagnostic = "Bearer prefix" + character + "never-publish-this" * 2000 + _LAST
                self.assertEqual(_file_tail(diagnostic), _CLIPPED)


if __name__ == "__main__":
    unittest.main()
