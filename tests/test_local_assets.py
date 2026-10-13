import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from tools.doom_companion.inventory import wad_lumps
from tools.doom_companion import local_assets
from tools.doom_companion.local_assets import build_local_cache, decode_patch, encode_patch, encode_wad, runtime_resources


def synthetic_iwad(game: str) -> bytes:
    lumps = {
        "PLAYPAL": bytes(value for color in range(256) for value in (color, color, color)),
        "INTERPIC": encode_patch([[32] * 8 for _ in range(8)]),
        "STBAR": encode_patch([[64] * 8 for _ in range(8)]),
        "CLIPA0": encode_patch([[96, None], [None, 96]], 1, 2),
        "STCFN065": encode_patch([[None, 200, None], [200, None, 200], [200, 200, 200]]),
    }
    lumps.update({"MAP01": b"", "MAP32": b""} if game == "doom2" else {"E1M1": b"", "E4M1": b""})
    payload = encode_wad(lumps)
    return b"IWAD" + payload[4:]


class DoomCompanionLocalAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.iwad = self.root / "DOOM2.WAD"
        self.iwad.write_bytes(synthetic_iwad("doom2"))
        self.cache = self.root / "private-cache"

    def test_patch_codec_round_trips_transparency_and_origins(self) -> None:
        pixels = [[None, 0, 255], [12, None, 255], [None, 32, None]]
        self.assertEqual((pixels, -2, 15), decode_patch(encode_patch(pixels, -2, 15)))

    def test_patch_codec_rejects_invalid_grids_and_offsets(self) -> None:
        for pixels in ([], [[]], [[0], [1, 2]], [[0]] * 256):
            with self.subTest(pixels=pixels), self.assertRaises(ValueError):
                encode_patch(pixels)
        raw = bytearray(encode_patch([[1, None], [None, 2]]))
        raw[8:12] = b"\xff" * 4
        with self.assertRaisesRegex(ValueError, "column"):
            decode_patch(bytes(raw))
        with self.assertRaises(ValueError):
            decode_patch(b"short")

    def test_resources_preserve_player_sprite_bytes_and_generate_new_markers(self) -> None:
        source = dict(wad_lumps(self.iwad.read_bytes()))
        payload = runtime_resources({"doom2": source}, [{"game": "doom2", "sha256": "test"}])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("APMine-local-assets.json"))
            self.assertTrue(manifest["private_cache"])
            self.assertFalse(manifest["release_ready"])
            self.assertEqual(["doom2"], manifest["supported_games"])
            game = dict(wad_lumps(archive.read("ArchipelagoDoom.wad")))
            self.assertEqual(source["CLIPA0"], game["APCLA0"])
            for name in ("APJIA0", "APPIA0", "WIURH0", "NOTIFBG", "STBAR"):
                pixels, _, _ = decode_patch(game[name])
                self.assertTrue(any(pixel is not None for row in pixels for pixel in row))
            self.assertEqual(576, len(decode_patch(game["STBAR"])[0][0]))
            self.assertEqual([], list(wad_lumps(archive.read("ArchipelagoHeretic.wad"))))

    def test_fonts_and_all_expected_animation_frames_are_valid_patches(self) -> None:
        source = dict(wad_lumps(self.iwad.read_bytes()))
        with zipfile.ZipFile(io.BytesIO(runtime_resources({"doom2": source}, []))) as archive:
            launcher = dict(wad_lumps(archive.read("Launcher.wad")))
            for code in range(33, 127):
                for prefix in ("F_SML", "F_LRG"):
                    decode_patch(launcher[f"{prefix}{code:03d}"])
            resources = dict(wad_lumps(archive.read("ArchipelagoDoom.wad")))
            for group, count in ((0, 3), (1, 6), (2, 1)):
                for index in range(count):
                    self.assertIn(f"WIA3{group:02d}{index:02d}", resources)

    def test_cache_is_repeatable_and_never_mutates_original_iwad(self) -> None:
        original = self.iwad.read_bytes()
        output = build_local_cache([self.iwad], self.cache)
        payload = output.read_bytes()
        self.assertEqual(output, build_local_cache([self.iwad], self.cache))
        self.assertEqual(payload, output.read_bytes())
        self.assertEqual(original, self.iwad.read_bytes())

    def test_corrupted_existing_cache_is_not_silently_reused_or_overwritten(self) -> None:
        output = build_local_cache([self.iwad], self.cache)
        output.write_bytes(b"wrong content")
        with self.assertRaisesRegex(ValueError, "Existing player cache"):
            build_local_cache([self.iwad], self.cache)
        self.assertEqual(b"wrong content", output.read_bytes())

    def test_both_games_supported_without_bundled_artwork_pack(self) -> None:
        other = self.root / "DOOM.WAD"
        other.write_bytes(synthetic_iwad("doom"))
        output = build_local_cache([other, self.iwad], self.cache)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(["doom", "doom2"], json.loads(archive.read("APMine-local-assets.json"))["supported_games"])

    def test_missing_duplicate_unsupported_and_repository_cache_inputs_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one"):
            build_local_cache([], self.cache)
        with self.assertRaisesRegex(ValueError, "one original"):
            build_local_cache([self.iwad, self.iwad], self.cache)
        self.iwad.write_bytes(encode_wad({"MAP01": b""}))
        with self.assertRaisesRegex(ValueError, "not a mod"):
            build_local_cache([self.iwad], self.cache)
        self.iwad.write_bytes(b"IWAD" + encode_wad({"E1M1": b""})[4:])
        with self.assertRaisesRegex(ValueError, "Unsupported IWAD"):
            build_local_cache([self.iwad], self.cache)
        repository = Path(local_assets.__file__).resolve().parents[2]
        with self.assertRaisesRegex(ValueError, "outside the platform"):
            build_local_cache([], repository / "build/apworlds")

    def test_cache_cannot_be_created_inside_companion_source_inputs(self) -> None:
        companion = self.root / "companion"
        companion.mkdir()
        (companion / "prepare-manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside companion"):
            build_local_cache([self.iwad], companion / "embed/private-cache")
        self.assertFalse((companion / "embed").exists())


if __name__ == "__main__":
    unittest.main()
