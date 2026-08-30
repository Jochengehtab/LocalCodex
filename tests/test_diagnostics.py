import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from local_codex.diagnostics import (
    DiagnosticReport,
    LocalArgumentError,
    inspect_codex_doctor,
    run_preflight,
    validate_local_arguments,
)


class FakeLocalStackHandler(BaseHTTPRequestHandler):
    available_models = ["plan", "build", "vision"]

    def do_GET(self):
        payload = {
            "/api/version": {"version": "test-ollama"},
            "/api/tags": {"models": [{"name": name} for name in self.available_models]},
            "/health": {"status": "ok", "pid": 123},
            "/v1/models": {
                "data": [{"id": "local-codex", "owned_by": "local"}]
            },
        }.get(self.path)
        if payload is None:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class DiagnosticsTests(unittest.TestCase):
    def test_cloud_and_provider_overrides_are_rejected(self):
        blocked = [
            ["--model", "gpt-5.6-luna"],
            ["--model=gpt-5.6-luna"],
            ["--oss"],
            ["--local-provider", "ollama"],
            ["--profile", "cloud"],
            ["--ignore-user-config"],
            ["-c", 'model_provider="openai"'],
            ["--config=model=\"gpt-5.6-luna\""],
        ]
        for arguments in blocked:
            with self.subTest(arguments=arguments), self.assertRaises(LocalArgumentError):
                validate_local_arguments(arguments)

    def test_safe_arguments_survive_and_force_is_removed(self):
        self.assertEqual(
            ["exec", "-C", "/tmp", "test"],
            validate_local_arguments(["--force", "exec", "-C", "/tmp", "test"]),
        )
        self.assertEqual([], validate_local_arguments(["--model", "local-codex"]))
        self.assertEqual(
            ["exec", "--", "--model", "gpt-is-just-prompt-text"],
            validate_local_arguments(["exec", "--", "--model", "gpt-is-just-prompt-text"]),
        )

    def test_doctor_requires_exact_local_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            payload = self.doctor_payload(home)
            checks = inspect_codex_doctor(payload, home)
        self.assertTrue(all(check.ok for check in checks))

    def test_doctor_detects_global_model(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            payload = self.doctor_payload(home)
            payload["checks"]["config.load"]["details"]["model"] = "gpt-5.6-luna"
            checks = inspect_codex_doctor(payload, home)
        self.assertFalse(checks[0].ok)

    def test_optional_warning_does_not_fail_report(self):
        report = DiagnosticReport()
        report.add("core", True, "ok")
        report.add("monitor", False, "offline", required=False)
        self.assertTrue(report.ok)

    def test_complete_preflight_against_fake_local_stack(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLocalStackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                report = run_preflight(
                    local_home=home,
                    ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    router_base_url=f"http://127.0.0.1:{server.server_port}",
                    required_models=("plan", "build", "vision"),
                    doctor_runner=lambda *_: self.doctor_payload(home),
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(7, len(report.checks))

    def test_preflight_detects_missing_ollama_alias(self):
        original = FakeLocalStackHandler.available_models
        FakeLocalStackHandler.available_models = ["plan", "build"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLocalStackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                report = run_preflight(
                    local_home=home,
                    ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    router_base_url=f"http://127.0.0.1:{server.server_port}",
                    required_models=("plan", "build", "vision"),
                    doctor_runner=lambda *_: self.doctor_payload(home),
                )
        finally:
            FakeLocalStackHandler.available_models = original
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(report.ok)
        model_check = next(check for check in report.checks if check.name == "Lokale Modelle")
        self.assertIn("vision", model_check.detail)

    @staticmethod
    def doctor_payload(home: Path):
        return {
            "checks": {
                "config.load": {
                    "status": "ok",
                    "details": {
                        "CODEX_HOME": str(home.resolve()),
                        "model": "local-codex",
                        "model provider": "local_router",
                    },
                },
                "auth.credentials": {
                    "status": "ok",
                    "details": {"model provider requires OpenAI auth": "false"},
                },
                "network.provider_reachability": {
                    "status": "ok",
                    "summary": "provider reachable",
                },
            }
        }


if __name__ == "__main__":
    unittest.main()
