from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.doom_companion import vendor_notices as notices
from tools.doom_companion.inventory import sha256
from tools.doom_companion.prepare import prepare_source


class DoomCompanionVendorNoticesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.raw = b"# synthetic vendored protocol\n"
        self.protocol = self.root / notices.PROTOCOL_PATH
        self.protocol.parent.mkdir(parents=True)
        self.hash_patch = patch.object(notices, "PROTOCOL_LF_SHA256", sha256(self.raw))
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def test_no_module_does_not_claim_a_notice_is_needed_or_present(self):
        self.assertEqual(({}, None), notices.collect(self.root))
        self.assertIsNone(notices.validate(self.root))

    def test_original_version_specific_notice_is_added_without_modifying_source(self):
        self.protocol.write_bytes(self.raw.replace(b"\n", b"\r\n"))
        files, profile = notices.collect(self.root)
        self.assertEqual(sha256(files[notices.NOTICE_PATH]), notices.NOTICE_SHA256)
        self.assertIn(b"2013-2019 Aymeric Augustin", files[notices.NOTICE_PATH])
        self.assertEqual(profile["source_sha256"], sha256(self.protocol.read_bytes()))
        self.assertEqual(profile["source_lf_sha256"], sha256(self.raw))
        self.assertFalse((self.root / notices.NOTICE_PATH).exists())
        with self.assertRaisesRegex(ValueError, "Missing.*vendor notice"):
            notices.validate(self.root)
        (self.root / notices.NOTICE_PATH).write_bytes(files[notices.NOTICE_PATH])
        self.assertEqual(notices.validate(self.root), profile)

    def test_changed_module_or_installed_notice_requires_review(self):
        self.protocol.write_bytes(self.raw + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "protocol differs"):
            notices.collect(self.root)
        self.protocol.write_bytes(self.raw)
        (self.root / notices.NOTICE_PATH).write_bytes(b"wrong component license")
        with self.assertRaisesRegex(ValueError, "notice differs"):
            notices.collect(self.root)

    def test_notice_template_is_hash_bound(self):
        self.protocol.write_bytes(self.raw)
        with patch.object(notices, "NOTICE_SHA256", "0" * 64), self.assertRaisesRegex(ValueError, "template changed"):
            notices.collect(self.root)

    def test_oversized_and_linked_inputs_are_rejected(self):
        self.protocol.write_bytes(b"x" * (notices.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "oversized"):
            notices.collect(self.root)
        self.protocol.unlink()
        target = self.root / "target.py"
        target.write_bytes(self.raw)
        try:
            self.protocol.symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "links"):
            notices.collect(self.root)

    def test_preparation_retains_notice_and_rejects_changed_input_before_creating_output(self):
        self.protocol.write_bytes(self.raw)
        (self.root / "COPYING.md").write_bytes(b"engine license")
        (self.root / "APCpp/LICENSE").write_bytes(b"network license")
        destination = self.root.parent / "prepared"
        manifest = prepare_source(self.root, destination)
        self.assertEqual(manifest["reviewed_vendor_notices"], notices.validate(destination))
        self.assertEqual(self.protocol.read_bytes(), (destination / notices.PROTOCOL_PATH).read_bytes())
        self.assertFalse((self.root / notices.NOTICE_PATH).exists())
        self.protocol.write_bytes(b"unreviewed protocol")
        invalid = self.root.parent / "invalid"
        with self.assertRaisesRegex(ValueError, "protocol differs"):
            prepare_source(self.root, invalid)
        self.assertFalse(invalid.exists())


if __name__ == "__main__":
    unittest.main()
