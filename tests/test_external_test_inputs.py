from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.doom_companion import external_test_inputs as external
from tools.doom_companion.prepare import prepare_source


class DoomExternalTestInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_boundary_preserves_notices_and_other_jsoncpp_data(self):
        for name in ("fail1.json", "nested/test.py", "PASS.JSON"):
            self.assertTrue(external.is_external_test_input(external.INPUT_ROOT + "/" + name))
        self.assertTrue(external.is_external_test_input(external.INPUT_ROOT.upper() + "/FAIL.JSON"))
        for name in ("readme.txt", "LICENSE", "COPYING", "NOTICE.md"):
            self.assertFalse(external.is_external_test_input(external.INPUT_ROOT + "/" + name))
        self.assertFalse(external.is_external_test_input("APCpp/jsoncpp/test/data/test.json"))
        self.assertFalse(external.is_external_test_input(external.INPUT_ROOT + "-other/test.json"))

    def test_validation_rejects_payload_even_with_different_directory_case(self):
        directory = self.root / external.INPUT_ROOT.upper()
        directory.mkdir(parents=True)
        (directory / "readme.txt").write_text("Original attribution")
        external.validate(self.root)
        (directory / "fail1.json").write_text("synthetic payload")
        with self.assertRaisesRegex(ValueError, "must not be distributed"):
            external.validate(self.root)

    def test_preparation_keeps_reference_harness_and_other_data_without_external_payloads(self):
        source = self.root / "source"
        files = {"COPYING.md": b"engine notice", "APCpp/LICENSE": b"network notice",
                 external.INPUT_ROOT + "/readme.txt": b"Original JSON_checker attribution",
                 external.INPUT_ROOT + "/fail1.json": b"synthetic unreviewed fixture",
                 "APCpp/jsoncpp/test/runjsontests.py": b"# original harness\n",
                 "APCpp/jsoncpp/test/data/test.json": b"synthetic library fixture"}
        files["APCpp/jsoncpp/test/data/test.expected"] = b"synthetic expected output"
        for name, raw in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        destination = self.root / "prepared"
        manifest = prepare_source(source, destination)
        self.assertEqual(manifest["external_test_inputs_policy"], external.policy())
        for name, raw in files.items():
            self.assertEqual((source / name).read_bytes(), raw)
            if external.is_external_test_input(name):
                self.assertFalse((destination / name).exists())
                self.assertIn(name, manifest["skipped_resources"])
            else:
                self.assertEqual((destination / name).read_bytes(), raw)
        external.validate(destination)

    def test_linked_fixture_directory_is_rejected(self):
        path = self.root / external.INPUT_ROOT
        path.parent.mkdir(parents=True)
        target = self.root / "outside"
        target.mkdir()
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "links"):
            external.validate(self.root)


if __name__ == "__main__":
    unittest.main()
