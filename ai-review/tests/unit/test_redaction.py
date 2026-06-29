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


if __name__ == "__main__":
    unittest.main()
