from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.doom_companion import font_sources
from tools.doom_companion.inventory import sha256
from tools.doom_companion.prepare import prepare_source


class ReviewedFontSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.payloads = {name: b"\x89PNG\0synthetic " + name.encode() for name in font_sources.FONT_FILES}
        self.payloads.update({name: b"Synthetic original notice or generator: " + name.encode()
                              for name in font_sources.CONTEXT_FILES})
        self.fonts = {name: sha256(raw) for name, raw in self.payloads.items() if name in font_sources.FONT_FILES}
        self.context = {name: sha256(raw) for name, raw in self.payloads.items() if name not in self.fonts}
        for name, raw in self.payloads.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    def collect(self):
        with patch.object(font_sources, "FONT_FILES", self.fonts), patch.object(font_sources, "CONTEXT_FILES", self.context):
            return font_sources.collect(self.root)

    def test_exact_reviewed_fonts_require_the_complete_original_notice_and_license_context(self):
        fonts, profile = self.collect()
        self.assertEqual(fonts, {name: self.payloads[name] for name in self.fonts})
        self.assertEqual(len(profile["inputs"]), 6)
        self.assertFalse(profile["source_delivery_complete"])
        self.assertFalse(profile["historical_corresponding_source_complete"])

    def test_production_review_does_not_accept_a_self_named_unreviewed_png(self):
        with self.assertRaisesRegex(ValueError, "differs from the immutable"):
            font_sources.collect(self.root)

    def test_partial_sets_changed_fonts_and_missing_or_changed_notices_are_rejected(self):
        for name in self.payloads:
            target = self.root / name
            target.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Missing"):
                self.collect()
            target.write_bytes(b"changed")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "differs"):
                self.collect()
            target.write_bytes(self.payloads[name])

    def test_legacy_without_pngs_is_not_reported_as_containing_preferred_png_source(self):
        for name in self.fonts:
            (self.root / name).unlink()
        self.assertEqual(({}, None), self.collect())

    def test_oversized_inputs_and_links_cannot_substitute_for_reviewed_bytes(self):
        with patch.object(font_sources, "MAX_INPUT_BYTES", 1), self.assertRaisesRegex(ValueError, "oversized"):
            self.collect()
        name = next(iter(self.fonts))
        target = self.root / name
        target.unlink()
        try:
            target.symlink_to(self.root / "COPYING.md")
        except OSError:
            self.skipTest("File links unavailable on this host")
        with self.assertRaisesRegex(ValueError, "links"):
            self.collect()

    def test_preparation_retains_only_the_reviewed_pngs_and_keeps_other_images_out(self):
        destination = self.root.parent / "prepared"
        (self.root / "APCpp").mkdir()
        (self.root / "APCpp/LICENSE").write_bytes(b"Synthetic library license")
        (self.root / "textscreen/fonts/private.png").write_bytes(b"private artwork")
        with patch.object(font_sources, "FONT_FILES", self.fonts), patch.object(font_sources, "CONTEXT_FILES", self.context):
            manifest = prepare_source(self.root, destination)
        for name in self.fonts:
            self.assertEqual(self.payloads[name], (destination / name).read_bytes())
            self.assertNotIn(name, manifest["skipped_resources"])
        self.assertFalse((destination / "textscreen/fonts/private.png").exists())
        self.assertEqual(len(manifest["reviewed_font_source_inputs"]["inputs"]), 6)


if __name__ == "__main__":
    unittest.main()
