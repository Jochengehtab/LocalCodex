"""Opt-in tests that use the installed Codex CLI and real local Ollama models.

Run with: LOCAL_CODEX_LIVE_TESTS=1 ./.venv/bin/python -m unittest tests.test_live_codex -v
"""

import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIVE = os.environ.get("LOCAL_CODEX_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE, "setzt LOCAL_CODEX_LIVE_TESTS=1 und echte Ollama-Modelle voraus")
class LiveCodexTests(unittest.TestCase):
    def test_full_self_test(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "start_codex.py"), "--self-test", "--json"],
            cwd=Path.home(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        report = json.loads(result.stdout[result.stdout.find("{"):])
        self.assertTrue(report["ok"], report)
        names = {check["name"] for check in report["checks"] if check["ok"]}
        self.assertIn("Echte Codex-Inferenz", names)
        self.assertIn("Ollama-Telemetrie", names)

    def test_interactive_tui_uses_local_model_and_answers(self):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "start_codex.py")],
            cwd=Path.home(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        output = bytearray()
        sentinel = f"LOCAL-TUI-LIVE-{os.getpid()}"
        try:
            self._read_until(master, output, b"local-codex xhigh", 45)
            os.write(master, f"Antworte exakt mit: {sentinel}".encode())
            time.sleep(0.2)
            # Codex enables the Kitty keyboard protocol; this is a real Enter key.
            os.write(master, b"\x1b[13u")
            self._read_until_visible(master, output, f"• {sentinel}", 180)
            os.write(master, b"\x03")
            time.sleep(0.3)
            os.write(master, b"\x03")
            self.assertEqual(0, process.wait(timeout=20), output.decode(errors="replace"))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            os.close(master)

    @staticmethod
    def _read_until(
        master: int,
        output: bytearray,
        expected: bytes,
        timeout: float,
        *,
        occurrences: int = 1,
    ) -> None:
        deadline = time.monotonic() + timeout
        while output.count(expected) < occurrences and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        if output.count(expected) < occurrences:
            raise AssertionError(
                f"{expected!r} nicht gefunden in:\n{output.decode(errors='replace')[-6000:]}"
            )

    @staticmethod
    def _read_until_visible(
        master: int, output: bytearray, expected: str, timeout: float
    ) -> None:
        deadline = time.monotonic() + timeout
        while expected not in LiveCodexTests._visible_text(output) and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        if expected not in LiveCodexTests._visible_text(output):
            raise AssertionError(
                f"{expected!r} nicht gefunden in:\n{LiveCodexTests._visible_text(output)[-6000:]}"
            )

    @staticmethod
    def _visible_text(output: bytearray) -> str:
        value = bytes(output)
        value = re.sub(rb"\x1b\][^\x07]*(?:\x07|\x1b\\)", b"", value)
        value = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", value)
        value = re.sub(rb"\x1b.", b"", value)
        return value.decode(errors="replace")


if __name__ == "__main__":
    unittest.main()
