import unittest

from local_codex.paths import map_cli_paths, map_windows_paths, windows_to_wsl


class PathTests(unittest.TestCase):
    def test_windows_drive_path_is_translated(self):
        self.assertEqual("/mnt/c/GitHub/FarmingGame", windows_to_wsl(r"C:\GitHub\FarmingGame"))
        self.assertEqual("/mnt/c/GitHub/FarmingGame", windows_to_wsl(r"C:\\GitHub\\FarmingGame"))
        self.assertEqual("/mnt/c/Program Files/FarmingGame", windows_to_wsl(r"C:\Program Files\FarmingGame"))
        self.assertIn("/mnt/c/GitHub/FarmingGame", map_windows_paths({"text": r"C:\GitHub\FarmingGame"})["text"])

    def test_codex_directory_options_are_translated(self):
        self.assertEqual(
            ["-C", "/mnt/c/GitHub/FarmingGame", "--add-dir=/mnt/d/Assets", "-i", "/mnt/c/tmp/farm.png"],
            map_cli_paths(
                [
                    "-C",
                    r"C:\GitHub\FarmingGame",
                    r"--add-dir=D:\Assets",
                    "-i",
                    r"C:\tmp\farm.png",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
