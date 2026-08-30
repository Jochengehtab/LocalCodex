import unittest

from local_codex.routing import TurnRouter


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.router = TurnRouter()

    def headers(self, turn="turn-1"):
        return {
            "x-codex-turn-metadata": (
                '{"session_id":"session-1","turn_id":"' + turn + '"}'
            )
        }

    def test_defaults_to_build_model(self):
        decision = self.router.choose({"input": "implement this"}, self.headers())
        self.assertEqual("local-codex-build:latest", decision.model)

    def test_plan_mode_uses_plan_model(self):
        body = {"input": "Please create a plan for this change."}
        decision = self.router.choose(body, self.headers())
        self.assertEqual("local-codex-plan:latest", decision.model)

    def test_host_instructions_do_not_force_plan_model(self):
        body = {
            "instructions": "Plan mode and review instructions live here.",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Plan mode"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Implement the change."}],
                },
            ],
        }
        decision = self.router.choose(body, self.headers("turn-build"))
        self.assertEqual("local-codex-build:latest", decision.model)

    def test_image_uses_vision_model(self):
        body = {"input": [{"type": "input_image", "image_url": "data:image/png;base64,x"}]}
        decision = self.router.choose(body, self.headers())
        self.assertEqual("local-codex-vision:latest", decision.model)

    def test_error_promotes_existing_turn(self):
        self.router.choose({"input": "implement"}, self.headers())
        body = {
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "tests failed with exit code 1",
                }
            ]
        }
        decision = self.router.choose(body, self.headers())
        self.assertEqual("local-codex-plan:latest", decision.model)

    def test_model_is_sticky_within_turn(self):
        first = self.router.choose(
            {"input": "Please plan the implementation."}, self.headers()
        )
        second = self.router.choose({"input": "continue"}, self.headers())
        self.assertEqual(first.model, second.model)


if __name__ == "__main__":
    unittest.main()
