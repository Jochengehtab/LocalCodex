import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReleaseVersionTests(unittest.TestCase):
    def test_set_version_accepts_semver_and_rejects_invalid_input(self):
        script = Path(__file__).parents[1] / "scripts" / "set_version.py"
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ}
            valid = subprocess.run(["python3", str(script), "1.2.3-rc.1"], cwd=directory, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(0, valid.returncode)
            self.assertEqual("1.2.3-rc.1", (Path(directory) / "VERSION").read_text().strip())
            invalid = subprocess.run(["python3", str(script), "latest"], cwd=directory, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(0, invalid.returncode)
