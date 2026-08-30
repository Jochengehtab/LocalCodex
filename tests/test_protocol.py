import json
import unittest

from local_codex.protocol import (
    canonical_arguments,
    canonical_tool_name,
    filter_input,
    response_as_sse,
    transform_sse,
)


class ProtocolTests(unittest.TestCase):
    def test_shell_aliases_are_canonicalized(self):
        offered = {"exec_command", "view_image"}
        self.assertEqual(
            "exec_command", canonical_tool_name("mcp__workspace__bash", offered)
        )
        arguments = canonical_arguments(
            "mcp__workspace__bash", "exec_command", '{"command":"pwd","cwd":"/tmp"}'
        )
        self.assertEqual({"cmd": "pwd", "workdir": "/tmp"}, json.loads(arguments))

    def test_windows_tool_paths_are_mapped_for_wsl(self):
        arguments = canonical_arguments(
            "Bash",
            "exec_command",
            {"command": "ls", "cwd": r"C:\GitHub\FarmingGame"},
        )
        self.assertEqual(
            {"cmd": "ls", "workdir": "/mnt/c/GitHub/FarmingGame"},
            json.loads(arguments),
        )

        image_arguments = canonical_arguments(
            "view_image", "view_image", {"file_path": r"C:\tmp\farm.png"}
        )
        self.assertEqual({"path": "/mnt/c/tmp/farm.png"}, json.loads(image_arguments))

    def test_patch_alias_becomes_safe_exec_command(self):
        patch = "*** Begin Patch\n*** Add File: x.txt\n+ok\n*** End Patch"
        arguments = canonical_arguments(
            "apply_patch", "exec_command", json.dumps({"patch": patch})
        )
        self.assertIn("apply_patch <<", json.loads(arguments)["cmd"])

    def test_search_aliases_target_namespaced_mcp_tools(self):
        offered = {
            "exec_command",
            "mcp__local_search__web_search",
            "mcp__local_search__fetch_page",
        }
        search = canonical_tool_name("browser_search", offered)
        fetch = canonical_tool_name("open_url", offered)
        self.assertEqual("mcp__local_search__web_search", search)
        self.assertEqual("mcp__local_search__fetch_page", fetch)
        self.assertEqual(
            {"query": "today"},
            json.loads(canonical_arguments("browser_search", search, {"q": "today"})),
        )
        self.assertEqual(
            {"url": "https://example.com"},
            json.loads(canonical_arguments("open_url", fetch, {"link": "https://example.com"})),
        )

    def test_sse_completed_response_is_normalized(self):
        payload = "\n\n".join(
            [
                'event: response.output_item.added\ndata: {"type":"response.output_item.added","item":{"id":"fc1","type":"function_call","name":"Bash","arguments":""}}',
                'event: response.function_call_arguments.done\ndata: {"type":"response.function_call_arguments.done","item_id":"fc1","arguments":"{\\"command\\":\\"pwd\\"}"}',
                'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp1","output":[{"id":"fc1","type":"function_call","name":"Bash","arguments":"{\\"command\\":\\"pwd\\"}"}]}}',
            ]
        )
        rendered, completed = transform_sse(payload, {"exec_command"})
        self.assertIn(b'"name":"exec_command"', rendered)
        self.assertEqual("exec_command", completed["output"][0]["name"])
        self.assertEqual({"cmd": "pwd"}, json.loads(completed["output"][0]["arguments"]))

    def test_host_skill_blocks_are_removed(self):
        items = [
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "<skills_instructions>large</skills_instructions>"},
                    {"type": "input_text", "text": "<permissions instructions>keep</permissions instructions>"},
                ],
            }
        ]
        filtered = filter_input(items)
        self.assertEqual(1, len(filtered[0]["content"]))
        self.assertIn("permissions", filtered[0]["content"][0]["text"])

    def test_complete_response_can_be_rendered_as_sse(self):
        response = {
            "id": "response-1",
            "output": [{"type": "message", "role": "assistant", "content": []}],
        }
        rendered = response_as_sse(response)
        self.assertIn(b"response.output_item.done", rendered)
        self.assertIn(b"response.completed", rendered)
        self.assertTrue(rendered.endswith(b"data: [DONE]\n\n"))

    def test_reasoning_items_and_events_are_removed(self):
        payload = "\n\n".join([
            'event: response.reasoning_summary_text.delta\ndata: {"type":"response.reasoning_summary_text.delta","delta":"secret"}',
            'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"type":"reasoning","summary":[{"text":"secret"}]}}',
            'event: response.completed\ndata: {"type":"response.completed","response":{"id":"r","output":[{"type":"reasoning","summary":[{"text":"secret"}]},{"type":"message","content":[]}]}}',
        ])
        rendered, completed = transform_sse(payload, set())
        self.assertNotIn(b"secret", rendered)
        self.assertEqual(["message"], [item["type"] for item in completed["output"]])


if __name__ == "__main__":
    unittest.main()
