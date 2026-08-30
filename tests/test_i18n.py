import os
import unittest
from unittest.mock import patch

from local_codex import i18n


class I18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_german_and_english_messages(self):
        i18n.set_language("de")
        self.assertIn("Release", i18n.tr("release.none"))
        i18n.set_language("en")
        self.assertIn("release", i18n.tr("release.none").lower())

    def test_locale_detection_prefers_explicit_environment(self):
        with patch.dict(os.environ, {"LOCAL_CODEX_LANGUAGE": "en", "LANG": "de_DE.UTF-8"}):
            # Reload-free public override is deterministic for running processes.
            self.assertEqual(i18n.set_language(os.environ["LOCAL_CODEX_LANGUAGE"]), "en")
            self.assertEqual(i18n.get_language(), "en")

    def test_default_is_english_when_no_override_is_set(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(i18n._detect_language(), "en")
