import unittest

from local_codex.app import _is_codex_title_request


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


if __name__ == "__main__":
    unittest.main()
