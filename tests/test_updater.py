import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater


class TestUpdateChecks(unittest.TestCase):
    digest = "ab" * 32

    def release(self, arch="x64", checksum=True):
        name = f"MInstAll_{arch}.exe"
        assets = [{"name": name, "browser_download_url": "https://example.test/app.exe", "size": 3}]
        if checksum:
            assets.append({"name": name + ".sha256", "browser_download_url": "https://example.test/hash"})
        return {"tag_name": "v2.3.0", "assets": assets}

    def test_valid_checksums_for_both_architectures(self):
        for arch in ("x86", "x64"):
            for suffix in ("", f"  MInstAll_{arch}.exe", f" *MInstAll_{arch}.exe"):
                with self.subTest(arch=arch, suffix=suffix), \
                     patch.object(updater, "current_arch", return_value=arch), \
                     patch.object(updater, "_fetch_json", return_value=self.release(arch)), \
                     patch.object(updater, "_fetch_text", return_value=self.digest.upper() + suffix + "\n"):
                    result = updater.check_for_updates("2.2.0")
                self.assertTrue(result["has_update"])
                self.assertEqual(result["sha256"], self.digest)

    def test_missing_checksum_blocks_update(self):
        with patch.object(updater, "current_arch", return_value="x64"), \
             patch.object(updater, "_fetch_json", return_value=self.release(checksum=False)):
            self.assertIn("error", updater.check_for_updates("2.2.0"))

    def test_missing_checksum_url_blocks_update(self):
        release = self.release()
        del release["assets"][1]["browser_download_url"]
        with patch.object(updater, "current_arch", return_value="x64"), \
             patch.object(updater, "_fetch_json", return_value=release):
            self.assertIn("error", updater.check_for_updates("2.2.0"))

    def test_unavailable_checksum_blocks_update(self):
        with patch.object(updater, "current_arch", return_value="x64"), \
             patch.object(updater, "_fetch_json", return_value=self.release()), \
             patch.object(updater, "_fetch_text", side_effect=OSError("offline")):
            self.assertIn("error", updater.check_for_updates("2.2.0"))

    def test_invalid_checksum_or_wrong_filename_blocks_update(self):
        for text in ("", "abc", "z" * 64, self.digest + "  MInstAll_x86.exe",
                     self.digest + "\n" + self.digest):
            with self.subTest(text=text), \
                 patch.object(updater, "current_arch", return_value="x64"), \
                 patch.object(updater, "_fetch_json", return_value=self.release()), \
                 patch.object(updater, "_fetch_text", return_value=text):
                self.assertIn("error", updater.check_for_updates("2.2.0"))

    def test_current_version_does_not_require_checksum(self):
        with patch.object(updater, "_fetch_json", return_value={"tag_name": "v2.2.0"}), \
             patch.object(updater, "_fetch_text") as fetch:
            result = updater.check_for_updates("2.2.0")
        self.assertFalse(result["has_update"])
        fetch.assert_not_called()


class TestApplyUpdate(unittest.TestCase):
    def test_invalid_digest_has_no_download_or_file_side_effects(self):
        for digest in (None, "", "abc", "z" * 64, 42):
            messages = []
            with self.subTest(digest=digest), \
                 patch.object(sys, "frozen", True, create=True), \
                 patch.object(updater, "_download_with_progress") as download, \
                 patch.object(updater.os, "remove") as remove, \
                 patch.object(updater.subprocess, "Popen") as launch:
                self.assertFalse(updater.download_and_update({"sha256": digest}, messages.append))
            download.assert_not_called()
            remove.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(messages[-1]["type"], "error")

    def apply_with_content(self, expected_hash):
        content = b"new executable"
        response = io.BytesIO(content)
        response.headers = {"Content-Length": str(len(content))}
        messages = []
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root, "app.exe")
            exe.write_bytes(b"original executable")
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(exe)), \
                 patch.dict(os.environ, {"TEMP": root}), \
                 patch.object(updater.urllib.request, "urlopen", return_value=response), \
                 patch.object(updater.subprocess, "Popen") as launch:
                if expected_hash == hashlib.sha256(content).hexdigest().upper():
                    with self.assertRaises(SystemExit):
                        updater.download_and_update({"url": "https://example.test/app.exe",
                                                     "sha256": expected_hash}, messages.append)
                    launch.assert_called_once()
                    self.assertTrue(Path(root, "minstall_updater.bat").exists())
                    self.assertEqual(Path(str(exe) + ".new").read_bytes(), content)
                    self.assertEqual(messages[-1]["type"], "done")
                else:
                    self.assertFalse(updater.download_and_update({"url": "https://example.test/app.exe",
                                                                 "sha256": expected_hash}, messages.append))
                    launch.assert_not_called()
                    self.assertFalse(Path(root, "minstall_updater.bat").exists())
                    self.assertFalse(Path(str(exe) + ".new").exists())
                    self.assertEqual(messages[-1]["type"], "error")
                self.assertEqual(exe.read_bytes(), b"original executable")

    def test_mismatch_never_launches_replacement(self):
        self.apply_with_content("0" * 64)

    def test_verified_download_can_launch_replacement(self):
        self.apply_with_content(hashlib.sha256(b"new executable").hexdigest().upper())
