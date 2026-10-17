import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.doom_companion import source_repository
from tools.doom_companion import font_sources
from tools.doom_companion import vendor_notices
from tools.doom_companion import external_test_inputs
from tools.doom_companion.inventory import sha256
from tools.doom_companion.source_repository import (
    ICONS, OWNED_NATIVE_ADDITIONS, modification_dates, stage_repository, verify_source_snapshot,
)


class DoomCompanionSourceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "prototype"
        self.destination = self.root / "source-candidate"
        self.prefix = self.root / "libraries"
        inputs = {
            "COPYING.md": "engine license", "APCpp/LICENSE": "GPL library license",
            "APCpp/IXWebSocket/LICENSE.txt": "BSD license", "APCpp/jsoncpp/LICENSE": "MIT license",
            "README.md": "original upstream attribution", "main.c": "// custom gameplay source\n",
            "CMakeLists.txt": "add_compile_definitions(APMINE_EXTERNAL_ASSETS_ONLY=1)\n",
            "src/CMakeLists.txt": "# builds only\n",
            "embed/CMakeLists.txt": "#define NUM_EMBEDDED_FILES 0\nmessage(FATAL_ERROR)\n",
            "src/setup/setup-manifest.xml.in": "<manifest/>",
            "src/doom-res.rc": '1 ICON "../data/doom.ico"',
            ".github/CONTRIBUTING.md": "inherited CI maintenance input",
            "create_release.bat": "old broad archive/publish script",
            "appimage.sh": "old downloader/icon packaging script",
            "pkg/package.py": "old platform packaging script",
            "mbedtls-3.4.0/LICENSE": "duplicate extraction omitted; source tar remains",
            "APCpp/jsoncpp/src/lib_json/json_valueiterator.inl": "// required inline source\n",
        }
        for name in ("doom", "heretic", "hexen", "strife", "setup", "launcher"):
            inputs[f"src/{name}-res.rc.in"] = "#include <windows.h>\n"
        for name, raw in inputs.items():
            self.write_source(name, raw.encode())
        for name in ICONS:
            self.write_source(name, b"static const unsigned int icon_data[] = {0};\n")
        # A source archive is an explicitly reviewed exception, not an art pack.
        self.write_source("APCpp/mbedtls-3.4.0.tar.gz", b"reviewed TLS source archive fixture")
        inputs["APCpp/mbedtls-3.4.0.tar.gz"] = "unused"
        prepared = {"status": "private-source-working-copy", "source_root": "/private/local/path",
                    "copied_inputs": [{"path": name, "sha256": sha256((self.source / name).read_bytes())} for name in inputs]}
        self.write_source("prepare-manifest.json", json.dumps(prepared).encode())
        directory = self.prefix / "share/libsamplerate"
        directory.mkdir(parents=True)
        (directory / "copyright").write_text("original BSD-2-Clause notice", encoding="utf-8")
        (directory / "vcpkg.spdx.json").write_text(json.dumps({"packages": [{
            "name": "libsamplerate", "versionInfo": "0.2.2", "downloadLocation": "https://example.test/source",
            "licenseDeclared": "NOASSERTION", "private-irrelevant-field": "/private/path",
        }]}), encoding="utf-8")

    def write_source(self, name: str, raw: bytes) -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def stage(self, dependency_sources=None) -> dict:
        return stage_repository(self.source, self.destination, self.prefix, dependency_sources)

    def write_snapshot(self, manifest: dict) -> None:
        (self.destination / "SOURCE_SNAPSHOT.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_vendored_protocol_gets_its_own_notice_and_profile_checks(self):
        raw = b"# synthetic vendored protocol\n"
        self.write_source(vendor_notices.PROTOCOL_PATH, raw)
        with patch.object(vendor_notices, "PROTOCOL_LF_SHA256", sha256(raw)):
            manifest = self.stage()
            self.assertEqual(manifest["reviewed_vendor_notices"], vendor_notices.validate(self.destination))
            self.assertFalse((self.source / vendor_notices.NOTICE_PATH).exists())
            self.assertEqual(verify_source_snapshot(self.destination), manifest)
            manifest["reviewed_vendor_notices"]["notice_sha256"] = "0" * 64
            self.write_snapshot(manifest)
            with self.assertRaisesRegex(ValueError, "vendor notice profile"):
                verify_source_snapshot(self.destination)

    def test_external_fixtures_are_omitted_from_legacy_preparation_and_late_additions(self):
        prefix = external_test_inputs.INPUT_ROOT
        legacy, late, notice = prefix + "/fail1.json", prefix + "/late.json", prefix + "/readme.txt"
        self.write_source(legacy, b"synthetic independent fixture")
        self.write_source(late, b"synthetic late fixture")
        self.write_source(notice, b"Original attribution")
        prepared = json.loads((self.source / "prepare-manifest.json").read_text())
        prepared["copied_inputs"].append({"path": legacy, "sha256": sha256((self.source / legacy).read_bytes())})
        self.write_source("prepare-manifest.json", json.dumps(prepared).encode())
        manifest = self.stage()
        for name in (legacy, late):
            self.assertFalse((self.destination / name).exists())
            self.assertTrue((self.source / name).exists())
        self.assertEqual((self.destination / notice).read_bytes(), b"Original attribution")
        self.assertEqual(verify_source_snapshot(self.destination), manifest)
        path = self.destination / legacy
        path.write_bytes(b"synthetic declared bypass")
        manifest["copied_inputs"].append({"path": legacy, "sha256": sha256(path.read_bytes())})
        self.write_snapshot(manifest)
        with self.assertRaisesRegex(ValueError, "External JSON_checker"):
            verify_source_snapshot(self.destination)

    def test_library_test_data_is_retained_while_engine_data_stays_excluded(self):
        fixtures = {"APCpp/jsoncpp/test/data/test.json": b"synthetic library fixture",
                    "APCpp/jsoncpp/test/data/test.expected": b"synthetic expected output"}
        for name, raw in fixtures.items():
            self.write_source(name, raw)
        self.write_source("data/game.json", b"synthetic unreviewed engine data")
        self.stage()
        for name, raw in fixtures.items():
            self.assertEqual((self.destination / name).read_bytes(), raw)
        self.assertFalse((self.destination / "data").exists())

    def add_owned_native_headers(self) -> None:
        helpers = Path(source_repository.__file__).resolve().parent
        for name in OWNED_NATIVE_ADDITIONS:
            self.write_source(name, (helpers / Path(name).name).read_bytes())

    def dependency_bundle(self):
        bundle = self.root / "dependency-source-bundle"
        payloads = {"sources/libsamplerate/source.c": b"/* synthetic library source */\n",
                    "licenses/libsamplerate.txt": (self.prefix / "share/libsamplerate/copyright").read_bytes()}
        for name, raw in payloads.items():
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        manifest = {"status": "private-dependency-source-bundle", "release_ready": False,
                    "dependencies": [{"name": "libsamplerate", "preferred_source": "sources/libsamplerate"}],
                    "copied_inputs": [{"path": name, "sha256": sha256(raw), "size": len(raw)}
                                      for name, raw in payloads.items()]}
        (bundle / "DEPENDENCY_SOURCES.json").write_text(json.dumps(manifest))
        return bundle

    def test_candidate_keeps_notices_helpers_custom_code_without_assets_or_git(self) -> None:
        self.write_source("embed/unlisted-private.png", b"game artwork; never copied")
        manifest = self.stage()
        self.assertFalse(manifest["release_ready"])
        self.assertFalse(manifest["published"])
        self.assertFalse((self.destination / ".git").exists())
        self.assertFalse((self.destination / "prepare-manifest.json").exists())
        for name in ("main.c", "APCpp/LICENSE", "APCpp/mbedtls-3.4.0.tar.gz",
                     "APCpp/jsoncpp/src/lib_json/json_valueiterator.inl", *ICONS):
            self.assertEqual((self.source / name).read_bytes(), (self.destination / name).read_bytes())
        self.assertEqual(b"original upstream attribution", (self.destination / "README.upstream.md").read_bytes())
        self.assertEqual((self.source / "APCpp/LICENSE").read_bytes(), (self.destination / "COPYING.GPL3.txt").read_bytes())
        self.assertFalse((self.destination / "src/doom-res.rc").exists())
        self.assertFalse((self.destination / ".github").exists())
        self.assertFalse((self.destination / "create_release.bat").exists())
        self.assertFalse((self.destination / "appimage.sh").exists())
        self.assertFalse((self.destination / "pkg").exists())
        self.assertFalse((self.destination / "mbedtls-3.4.0").exists())

        self.assertFalse((self.destination / "embed/unlisted-private.png").exists())
        self.assertTrue((self.destination / "tools/doom_companion/local_assets.py").is_file())
        self.assertTrue((self.destination / "tools/doom_companion/bootstrap.py").is_file())
        self.assertTrue((self.destination / "tests/test_bootstrap.py").is_file())
        self.assertTrue((self.destination / "tools/doom_companion/release_source_link.py").is_file())
        self.assertTrue((self.destination / "tests/test_release_source_link.py").is_file())
        self.assertTrue((self.destination / "tests/test_handshake.py").is_file())
        self.assertTrue((self.destination / "tests/test_source_repository.py").is_file())
        self.assertTrue((self.destination / "tools/doom_companion/ln_handshake.h").is_file())
        for name in ("gameplay", "practice_items", "network_gameplay", "source_access"):
            self.assertTrue((self.destination / f"tests/test_{name}.py").is_file())
        for name in ("gameplay.py", "apmine_gameplay_test.h", "practice_items.h"):
            self.assertTrue((self.destination / f"tools/doom_companion/{name}").is_file())
        self.assertTrue((self.destination / "BUILDING.md").is_file())
        self.assertNotIn("/private/local/path", json.dumps(manifest))
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_font_notice_and_extensionless_generator_are_discovered_even_if_old_manifest_omitted_them(self) -> None:
        payloads = {"textscreen/fonts/README": b"Synthetic font copyright and license\n",
                    "textscreen/fonts/convert-font": b"#!/usr/bin/env python3\n# synthetic generator\n"}
        for name, raw in payloads.items():
            self.write_source(name, raw)
        self.write_source("textscreen/fonts/private.png", b"synthetic private image")
        manifest = self.stage()
        for name, raw in payloads.items():
            self.assertEqual(raw, (self.destination / name).read_bytes())
        self.assertFalse((self.destination / "textscreen/fonts/private.png").exists())
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_dependency_references_are_not_falsely_marked_as_delivered_source(self) -> None:
        self.stage()
        dependencies = json.loads((self.destination / "DEPENDENCIES.json").read_text())
        self.assertFalse(dependencies["source_delivery_complete"])
        self.assertIn("not-included", dependencies["installed_dependencies"][0]["source_delivery"])
        self.assertNotIn("/private/path", json.dumps(dependencies))
        self.assertEqual("original BSD-2-Clause notice", (self.destination / "licenses/dependencies/libsamplerate.txt").read_text())

    def test_declaring_unreviewed_media_in_the_snapshot_does_not_make_it_source(self) -> None:
        manifest = self.stage()
        name, raw = "unreviewed.png", b"synthetic private artwork"
        (self.destination / name).write_bytes(raw)
        manifest["copied_inputs"].append({"path": name, "sha256": sha256(raw)})
        self.write_snapshot(manifest)
        with self.assertRaisesRegex(ValueError, "Unreviewed source/asset"):
            verify_source_snapshot(self.destination)

    def test_historical_converter_requires_exact_reviewed_bytes_and_path(self) -> None:
        manifest = self.stage()
        reviewed = next(iter(source_repository.REVIEWED_HISTORICAL_SOURCES))
        for name, message in ((reviewed, "Historical source differs"),
                              (reviewed.replace("20261016", "unreviewed"), "Unreviewed source/asset")):
            with self.subTest(name=name):
                path = self.destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = b"#!/bin/sh\n# Arbitrary content must not gain approval from its filename.\n"
                path.write_bytes(raw)
                manifest["copied_inputs"].append({"path": name, "sha256": sha256(raw)})
                self.write_snapshot(manifest)
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        verify_source_snapshot(self.destination)
                finally:
                    manifest["copied_inputs"].pop()
                    path.unlink()
        self.write_snapshot(manifest)
        self.assertEqual(verify_source_snapshot(self.destination), manifest)

    def test_reviewed_font_pngs_are_staged_with_their_bound_profile_and_notices(self) -> None:
        payloads = {name: b"\x89PNG\0synthetic font " + name.encode() for name in font_sources.FONT_FILES}
        payloads.update({"COPYING.md": (self.source / "COPYING.md").read_bytes(),
                         "textscreen/fonts/README": b"Synthetic upstream font copyright and grant",
                         "textscreen/fonts/convert-font": b"# Synthetic upstream generator"})
        for name, raw in payloads.items():
            self.write_source(name, raw)
        fonts = {name: sha256(payloads[name]) for name in font_sources.FONT_FILES}
        context = {name: sha256(payloads[name]) for name in font_sources.CONTEXT_FILES}
        with patch.object(font_sources, "FONT_FILES", fonts), patch.object(font_sources, "CONTEXT_FILES", context):
            manifest = self.stage()
            self.assertEqual(6, len(manifest["reviewed_font_source_inputs"]["inputs"]))
            self.assertEqual(manifest, verify_source_snapshot(self.destination))
            for name in fonts:
                self.assertEqual(payloads[name], (self.destination / name).read_bytes())
            manifest["reviewed_font_source_inputs"]["upstream_revision"] = "0" * 40
            self.write_snapshot(manifest)
            with self.assertRaisesRegex(ValueError, "font source profile"):
                verify_source_snapshot(self.destination)

    def test_old_font_arrays_cannot_be_staged_without_their_notice_and_generator(self) -> None:
        self.write_source("textscreen/fonts/normal.h", b"/* Please see textscreen/fonts/README for copyright information. */\n")
        for missing in ("README", "convert-font"):
            with self.assertRaisesRegex(ValueError, f"textscreen/fonts/{missing}"):
                self.stage()
            self.assertFalse(self.destination.exists())
            self.write_source(f"textscreen/fonts/{missing}", b"# synthetic required upstream text\n")
        manifest = self.stage()
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_assembled_candidate_includes_verified_dependency_source_and_build_tools(self) -> None:
        bundle = self.dependency_bundle()
        manifest = self.stage(bundle)
        self.assertTrue(manifest["required_dependency_sources_present"])
        vendor = self.destination / "vendor/native-dependencies"
        self.assertEqual((bundle / "sources/libsamplerate/source.c").read_bytes(),
                         (vendor / "sources/libsamplerate/source.c").read_bytes())
        dependencies = json.loads((self.destination / "DEPENDENCIES.json").read_text())
        self.assertTrue(dependencies["required_dependency_sources_present"])
        self.assertFalse(dependencies["source_delivery_complete"])
        self.assertIn("private candidate", dependencies["installed_dependencies"][0]["source_delivery"])
        self.assertTrue((self.destination / "tools/doom_companion/build_dependencies.ps1").is_file())
        self.assertTrue((self.destination / "tests/test_dependency_sources.py").is_file())
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_dependency_notice_version_mismatch_is_rejected_before_assembly(self) -> None:
        bundle = self.dependency_bundle()
        path = bundle / "licenses/libsamplerate.txt"
        path.write_bytes(b"different notice version")
        record = bundle / "DEPENDENCY_SOURCES.json"
        manifest = json.loads(record.read_text())
        entry = next(entry for entry in manifest["copied_inputs"] if entry["path"] == "licenses/libsamplerate.txt")
        entry.update(sha256=sha256(path.read_bytes()), size=path.stat().st_size)
        record.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "notice version differs"):
            self.stage(bundle)
        self.assertFalse(self.destination.exists())

    def test_source_subdirectory_named_build_is_not_an_output_exemption(self) -> None:
        self.stage()
        path = self.destination / "src/doom/build-assets/unsafe.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unreviewed artwork")
        with self.assertRaisesRegex(ValueError, "Unreviewed extra"):
            verify_source_snapshot(self.destination)

    def test_native_handshake_must_match_the_exact_protocol_test_header(self) -> None:
        self.write_source("src/launcher/ln_handshake.h", b"/* different protocol implementation */\n")
        with self.assertRaisesRegex(ValueError, "handshake header differs"):
            self.stage()
        self.assertFalse(self.destination.exists())

    def test_native_gameplay_and_practice_headers_must_match_tested_inputs(self) -> None:
        for name in ("src/doom/apmine_gameplay_test.h", "src/doom/apmine_network_test.h", "src/archipelago/practice_items.h"):
            with self.subTest(name=name):
                self.write_source(name, b"/* different native acceptance source */\n")
                with self.assertRaisesRegex(ValueError, "differs from the acceptance-test input"):
                    self.stage()
                self.assertFalse(self.destination.exists())
                (self.source / name).unlink()

    def test_required_source_added_after_preparation_is_included_and_recorded(self) -> None:
        name = "APCpp/jsoncpp/src/lib_json/json_valueiterator.inl"
        path = self.source / "prepare-manifest.json"
        prepared = json.loads(path.read_text())
        prepared["copied_inputs"] = [entry for entry in prepared["copied_inputs"] if entry["path"] != name]
        path.write_text(json.dumps(prepared), encoding="utf-8")
        manifest = self.stage()
        self.assertEqual((self.source / name).read_bytes(), (self.destination / name).read_bytes())
        self.assertIn(name, [entry["path"] for entry in manifest["source_additions_since_preparation"]])
        added = next(entry for entry in manifest["source_additions_since_preparation"] if entry["path"] == name)
        self.assertNotIn("modification_dates", added)

    def test_modified_source_requires_prominent_valid_date_before_assembly(self) -> None:
        cases = ((b"// altered source without date\n", "Missing prominent"),
                 (b"/* APMine companion change, 2026-02-30: modified. */\n", "Invalid companion modification date"),
                 (b" " * 4096 + b"\n// APMine companion change, 2026-10-12: late notice.\n", "Missing prominent"))
        for raw, message in cases:
            with self.subTest(message=message, raw=raw[:80]):
                self.write_source("main.c", raw)
                with self.assertRaisesRegex(ValueError, message):
                    self.stage()
                self.assertFalse(self.destination.exists())

    def test_dates_are_recorded_without_rewriting_source_or_copyrights(self) -> None:
        original = (self.source / "main.c").read_bytes()
        raw = (b"/* APMine companion change, 2026-10-12: tested integration. */\n"
               b"/* Copyright (C) original author; original terms retained. */\n" + original)
        self.write_source("main.c", raw)
        manifest = self.stage()
        changes = manifest["changes_from_prepared_baseline"]
        self.assertEqual([{"path": "main.c", "before_sha256": sha256(original), "after_sha256": sha256(raw),
                           "modification_dates": ["2026-10-12"]}], changes)
        self.assertEqual(raw, (self.destination / "main.c").read_bytes())
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_changed_upstream_readme_is_checked_at_renamed_destination(self) -> None:
        raw = b"<!-- APMine companion change, 2026-10-12: integration note. -->\noriginal upstream attribution\n"
        self.write_source("README.md", raw)
        manifest = self.stage()
        self.assertEqual(raw, (self.destination / "README.upstream.md").read_bytes())
        self.assertEqual(["2026-10-12"], manifest["changes_from_prepared_baseline"][0]["modification_dates"])
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_upstream_standalone_license_edits_are_rejected_even_with_a_date(self) -> None:
        for name in ("COPYING.md", "APCpp/LICENSE", "APCpp/IXWebSocket/LICENSE.txt", "APCpp/jsoncpp/LICENSE"):
            previous = (self.source / name).read_bytes()
            with self.subTest(name=name):
                self.write_source(name, b"// APMine companion change, 2026-10-12: altered grant.\n" + previous)
                with self.assertRaisesRegex(ValueError, "Upstream copyright/license notice changed"):
                    self.stage()
                self.assertFalse(self.destination.exists())
            self.write_source(name, previous)

    def test_notice_parser_accepts_comment_styles_and_deduplicates_dates(self) -> None:
        for style in (b"#", b"//", b"/*", b"<!--"):
            with self.subTest(style=style):
                raw = (style + b" APMine companion change, 2026-10-12: current.\n" +
                       style + b" APMine companion change, 2026-10-11. previous.\n" +
                       style + b" APMine companion change, 2026-10-12: duplicate.\n")
                self.assertEqual(["2026-10-11", "2026-10-12"], modification_dates(raw, "synthetic"))

    def test_owned_native_additions_keep_exact_tested_bytes_and_dated_records(self) -> None:
        self.add_owned_native_headers()
        manifest = self.stage()
        for name in OWNED_NATIVE_ADDITIONS:
            with self.subTest(name=name):
                entry = next(entry for entry in manifest["source_additions_since_preparation"] if entry["path"] == name)
                expected_date = "2026-10-13" if name in {
                    "src/doom/level_select_layout.h", "src/doom/pickup_patch.h",
                    "src/doom/pickup_sprites.c", "src/doom/pickup_sprites.h"} else "2026-10-12"
                self.assertEqual([expected_date], entry["modification_dates"])
                self.assertEqual((self.source / name).read_bytes(), (self.destination / name).read_bytes())
        self.assertEqual(manifest, verify_source_snapshot(self.destination))

    def test_missing_or_malformed_notice_profile_is_rejected(self) -> None:
        manifest = self.stage()
        for value in (None, True, False, "1", 1.0, 0, 2):
            with self.subTest(value=value):
                manifest["modification_notice_profile"] = value
                self.write_snapshot(manifest)
                with self.assertRaisesRegex(ValueError, "dated-modification notice profile"):
                    verify_source_snapshot(self.destination)
        manifest.pop("modification_notice_profile")
        self.write_snapshot(manifest)
        with self.assertRaisesRegex(ValueError, "dated-modification notice profile"):
            verify_source_snapshot(self.destination)

    def test_changed_source_notice_record_tampering_is_rejected(self) -> None:
        self.write_source("main.c", b"// APMine companion change, 2026-10-12: modified.\n")
        manifest = self.stage()
        entry = manifest["changes_from_prepared_baseline"][0]
        previous = dict(entry)
        for updates in ({"modification_dates": ["2026-10-11"]}, {"after_sha256": "wrong"}):
            with self.subTest(updates=updates):
                entry.update(updates)
                self.write_snapshot(manifest)
                with self.assertRaisesRegex(ValueError, "notice/hash record differs"):
                    verify_source_snapshot(self.destination)
            entry.update(previous)
        manifest["changes_from_prepared_baseline"].append(dict(entry))
        self.write_snapshot(manifest)
        with self.assertRaisesRegex(ValueError, "duplicate modified source inventory"):
            verify_source_snapshot(self.destination)

    def test_owned_native_notice_records_cannot_be_removed_or_altered(self) -> None:
        self.add_owned_native_headers()
        manifest = self.stage()
        entry = next(entry for entry in manifest["source_additions_since_preparation"]
                     if entry["path"] == "src/launcher/ln_handshake.h")
        previous = dict(entry)
        for updates, message in (({"modification_dates": []}, "addition notice record differs"),
                                  ({"sha256": "wrong"}, "addition hash record differs")):
            with self.subTest(updates=updates):
                entry.update(updates)
                self.write_snapshot(manifest)
                with self.assertRaisesRegex(ValueError, message):
                    verify_source_snapshot(self.destination)
            entry.update(previous)
        manifest["source_additions_since_preparation"].remove(entry)
        self.write_snapshot(manifest)
        with self.assertRaisesRegex(ValueError, "Missing companion native notice records"):
            verify_source_snapshot(self.destination)

    def test_source_edits_and_unreviewed_extra_assets_fail_verification(self) -> None:
        self.stage()
        path = self.destination / "main.c"
        previous = path.read_bytes()
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash changed"):
            verify_source_snapshot(self.destination)
        path.write_bytes(previous)
        extra = self.destination / "build-assets.pk3"
        extra.write_bytes(b"unreviewed")
        with self.assertRaisesRegex(ValueError, "Unreviewed extra"):
            verify_source_snapshot(self.destination)
        extra.unlink()
        (self.destination / "build-local/bin").mkdir(parents=True)
        (self.destination / "build-local/bin/engine.exe").write_bytes(b"local compiled output")
        verify_source_snapshot(self.destination)

    def test_refuses_existing_and_overlapping_targets(self) -> None:
        self.stage()
        with self.assertRaises(FileExistsError):
            self.stage()
        for destination in (self.source / "nested", self.root):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                stage_repository(self.source, destination, self.prefix)

    def test_art_arrays_embedding_and_publication_hooks_fail_before_copying(self) -> None:
        for name, unsafe in (("src/doom/doom_icon.c", b"static unsigned int icon_data[] = {1,2,3};"),
                             ("CMakeLists.txt", b"APDOOM_WEB_DOWNLOAD_DIR"),
                             ("embed/CMakeLists.txt", b"embed_assets()"),
                             ("src/doom-res.rc.in", b'1 ICON "doom.ico"')):
            path = self.source / name
            previous = path.read_bytes()
            path.write_bytes(unsafe)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.destination.exists())
            path.write_bytes(previous)

    def test_asset_archive_and_traversal_in_preparation_manifest_fail(self) -> None:
        path = self.source / "prepare-manifest.json"
        manifest = json.loads(path.read_text())
        for name in ("../escaping.c", "/absolute.c", "embed/art.wad"):
            manifest["copied_inputs"].append({"path": name, "sha256": "unreviewed"})
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.destination.exists())
            manifest["copied_inputs"].pop()

    def test_changed_tls_source_requires_fresh_review(self) -> None:
        self.write_source("APCpp/mbedtls-3.4.0.tar.gz", b"unreviewed archive")
        with self.assertRaisesRegex(ValueError, "TLS source archive changed"):
            self.stage()
        self.assertFalse(self.destination.exists())

    def test_missing_dependency_notices_fail_before_creating_destination(self) -> None:
        (self.prefix / "share/libsamplerate/copyright").unlink()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
