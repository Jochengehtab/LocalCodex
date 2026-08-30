import unittest
from unittest.mock import MagicMock, patch

import start_codex


class LauncherTests(unittest.TestCase):
    def test_setup_context_arguments_are_forwarded(self):
        self.assertEqual(
            ["--context", "65536"],
            start_codex.setup_context_arguments(["--benchmark", "--context", "65536"]),
        )
        self.assertEqual(
            ["--context=131072"],
            start_codex.setup_context_arguments(["--context=131072"]),
        )
        self.assertEqual([], start_codex.setup_context_arguments(["--benchmark"]))

    @patch("start_codex.subprocess.Popen")
    @patch("start_codex.router_ready", side_effect=[False, True])
    def test_router_does_not_inherit_tui_stdin(self, _ready, popen):
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        started = start_codex.start_router()
        self.assertIs(started, process)
        self.assertIs(popen.call_args.kwargs["stdin"], start_codex.subprocess.DEVNULL)

    @patch("start_codex.subprocess.Popen")
    @patch("start_codex._monitor_runtime", return_value={"wsl_executable": "/bin/true"})
    def test_monitor_does_not_inherit_tui_stdin(self, _runtime, popen):
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        started = start_codex.start_monitor(True)
        self.assertIs(started, process)
        self.assertIs(popen.call_args.kwargs["stdin"], start_codex.subprocess.DEVNULL)

if __name__ == "__main__":
    unittest.main()
