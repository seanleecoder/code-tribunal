from __future__ import annotations

import unittest

from ai_review.redact import redact_text


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


if __name__ == "__main__":
    unittest.main()
