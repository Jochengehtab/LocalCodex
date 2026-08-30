import tempfile
import unittest
from pathlib import Path

from local_codex.state import ResponseStateStore


class StateTests(unittest.TestCase):
    def test_previous_response_is_expanded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResponseStateStore(Path(directory) / "state.sqlite3", 3600, 10)
            initial = [
                {
                    "type": "message",
                    "id": "user-1",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "run pwd"}],
                }
            ]
            output = [
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "exec_command",
                    "arguments": '{"cmd":"pwd"}',
                }
            ]
            store.put("resp-1", "session", "turn", initial, output)
            expanded = store.expand(
                "resp-1",
                [
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "/tmp",
                    }
                ],
            )
            self.assertEqual(["message", "function_call", "function_call_output"], [x["type"] for x in expanded])


if __name__ == "__main__":
    unittest.main()
