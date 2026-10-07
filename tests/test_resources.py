import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import i18n
import profiles


class TestResources(unittest.TestCase):
    def test_external_resource_overrides_bundle(self):
        with tempfile.TemporaryDirectory() as external, tempfile.TemporaryDirectory() as bundle:
            for root in (external, bundle):
                Path(root, "profiles").mkdir()
            with patch.object(config, "SCRIPT_DIR", external), \
                 patch.object(sys, "_MEIPASS", bundle, create=True):
                self.assertEqual(config.resource_path("profiles"), os.path.join(external, "profiles"))

    def test_frozen_bundle_resources_load_without_adjacent_files(self):
        import shutil
        with tempfile.TemporaryDirectory() as external, tempfile.TemporaryDirectory() as bundle:
            for directory in ("i18n", "profiles", "icons"):
                shutil.copytree(Path(__file__).resolve().parents[1] / directory, Path(bundle) / directory)
            with patch.object(config, "SCRIPT_DIR", external), \
                 patch.object(sys, "_MEIPASS", bundle, create=True):
                with patch.object(i18n, "I18N_DIR", config.resource_path("i18n")), \
                     patch.object(profiles, "PROFILES_DIR", config.resource_path("profiles")):
                    self.assertTrue(i18n._load_translations_file("en"))
                    self.assertTrue(i18n._load_translations_file("ru"))
                    self.assertEqual(len(profiles.list_profiles()), 3)
                self.assertTrue(Path(config.resource_path("icons/system.png")).is_file())

    def test_missing_resource_keeps_external_path(self):
        with tempfile.TemporaryDirectory() as external:
            with patch.object(config, "SCRIPT_DIR", external):
                self.assertEqual(config.resource_path("missing-resource.json"),
                                 os.path.join(external, "missing-resource.json"))
