import unittest
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import start_codex
from local_codex.config import ModelConfig


class LauncherTests(unittest.TestCase):
    @patch("start_codex.subprocess.run")
    def test_changed_profiles_trigger_alias_setup_not_config_refresh(self, run):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").touch()
            (home / "localcodex.toml").write_text(
                'schema_version = 1\n[models.build]\ncontext = 16384\n', encoding="utf-8"
            )
            (home / "runtime.json").write_text(json.dumps({
                "config_version": 11, "model_profiles": asdict(ModelConfig()),
                "monitor": {"wsl_executable": str(home / "config.toml")},
            }), encoding="utf-8")
            with patch("start_codex.LOCAL_HOME", home):
                start_codex.ensure_setup(refresh_config=True)
            self.assertNotIn("--refresh-config", run.call_args.args[0])
            self.assertIn("local_codex.setup", run.call_args.args[0])

    @patch("start_codex.subprocess.run")
    def test_unchanged_profiles_do_not_recreate_aliases(self, run):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").touch()
            (home / "runtime.json").write_text(json.dumps({
                "config_version": 11, "model_profiles": asdict(ModelConfig()),
                "monitor": {"wsl_executable": str(home / "config.toml")},
            }), encoding="utf-8")
            with patch("start_codex.LOCAL_HOME", home):
                start_codex.ensure_setup()
            run.assert_not_called()

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

    @patch("start_codex.subprocess.run")
    def test_refresh_config_is_forwarded_without_full_install(self, run):
        with patch("start_codex.LOCAL_HOME") as local_home:
            local_home.__truediv__.return_value.exists.return_value = True
            local_home.__truediv__.return_value.is_file.return_value = False
            local_home.__truediv__.return_value.read_text.return_value = "{}"
            start_codex.ensure_setup(refresh_config=True, build_monitor=True)
        command = run.call_args.args[0]
        self.assertIn("--refresh-config", command)
        self.assertIn("--build-monitor", command)

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

    @patch("start_codex.subprocess.run")
    def test_benchmark_command_is_forwarded(self, run):
        run.return_value.returncode = 0
        with patch("start_codex.map_cli_paths", return_value=["benchmark", "--json"]):
            with patch("start_codex.sys.argv", ["start_codex.py", "benchmark", "--json"]):
                self.assertEqual(0, start_codex.main())
        command = run.call_args.args[0]
        self.assertIn("--benchmark", command)
        self.assertIn("--json", command)

if __name__ == "__main__":
    unittest.main()
