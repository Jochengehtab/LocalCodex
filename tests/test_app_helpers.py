import unittest
from types import SimpleNamespace

from local_codex.app import _is_codex_title_request, _prepare_body


class AppHelperTests(unittest.TestCase):
    def test_codex_title_request_is_separate_from_user_turn(self):
        self.assertTrue(_is_codex_title_request([{
            "role": "user",
            "content": [{"type": "input_text", "text": "Generate a concise, single-line task title of at most 36 characters."}],
        }]))

    def test_normal_prompt_is_displayed(self):
        self.assertFalse(_is_codex_title_request([{
            "role": "user",
            "content": [{"type": "input_text", "text": "Bitte generiere komplizierten C++ Code."}],
        }]))

    def test_upstream_request_never_requests_reasoning_summary(self):
        decision = SimpleNamespace(model="local-codex-build:latest")
        prepared, _ = _prepare_body(
            {"input": "test", "reasoning": {"summary": "auto", "effort": "low"}},
            {}, decision,
        )
        self.assertEqual("xhigh", prepared["reasoning"]["effort"])
        self.assertNotIn("summary", prepared["reasoning"])


if __name__ == "__main__":
    unittest.main()
