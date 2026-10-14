import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.doom_companion import release_source_link as binding
from tools.doom_companion.dependency_sources import digest


class DoomReleaseSourceLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.build = self.root / "native-build"
        self.dependency_build = self.root / "dependency-build"
        self.prefix = self.dependency_build / "installed"
        self.vendor = self.source / "vendor/native-dependencies"
        for directory in (self.source, self.build / "bin", self.prefix / "bin", self.vendor / "metadata/example"):
            directory.mkdir(parents=True)
        (self.source / "SOURCE_SNAPSHOT.json").write_text("{}")
        (self.vendor / "DEPENDENCY_SOURCES.json").write_text("{}")
        (self.vendor / "metadata/example/vcpkg.spdx.json").write_text(json.dumps({
            "files": [{"fileName": "./bin/example.dll"}]}))
        (self.dependency_build / "dependency-build.json").write_text(json.dumps({
            "bundle": str(self.vendor), "snapshot_sha256": digest(self.vendor / "DEPENDENCY_SOURCES.json")}))
        self.dependency_cache = self.dependency_build / "example/CMakeCache.txt"
        self.dependency_cache.parent.mkdir()
        self.dependency_cache.write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={self.vendor / 'sources/example'}\n"
            f"CMAKE_INSTALL_PREFIX:PATH={self.prefix}\n")
        self.native_cache = self.build / "CMakeCache.txt"
        self.native_cache.write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={self.source}\nCMAKE_PREFIX_PATH:UNINITIALIZED={self.prefix}\n" +
            "".join(f"{key}:PATH={self.prefix / 'lib/cmake/package'}\n"
                    for key in ("SDL2_DIR", "SDL2_mixer_DIR", "PNG_DIR", "SampleRate_DIR", "ZLIB_DIR")))
        (self.build / "embedded_files.h").write_text("#define NUM_EMBEDDED_FILES 0\n")
        for name in ("archipelago-doom.exe", "apdoom-launcher.exe", "apdoom-setup.exe"):
            (self.build / "bin" / name).write_bytes(b"MZsynthetic executable")
        (self.prefix / "bin/example.dll").write_bytes(b"MZsynthetic library")
        (self.source / "tools/doom_companion").mkdir(parents=True)
        for name in ("build_local.ps1", "build_dependencies.ps1"):
            (self.source / "tools/doom_companion" / name).write_text("# synthetic recipe\n")
        self.snapshot = {"required_dependency_sources_present": True, "copied_inputs": [{"path": "synthetic"}]}
        self.dependencies = {"dependencies": [{"name": "example", "preferred_source": "sources/example"}]}

    def imports(self, path):
        return ["KERNEL32.dll"] if path.suffix == ".dll" else ["example.dll"]

    def link(self, imports=None):
        with patch.object(binding, "verify_source_snapshot", return_value=self.snapshot), \
                patch.object(binding, "verify", return_value=self.dependencies):
            return binding.link(self.source, self.build, self.prefix, imports or self.imports)

    def test_record_links_every_runtime_binary_without_claiming_public_source_delivery(self):
        report = self.link()
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["published"])
        self.assertIsNone(report["source_access"]["url"])
        self.assertEqual(4, len(report["binaries"]))
        self.assertEqual({"engine/APCpp", "example"}, {item["source_component"] for item in report["binaries"]})
        self.assertIs(report["gameplay_diagnostics"], False)
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_reference_only_candidate_is_rejected(self):
        self.snapshot["required_dependency_sources_present"] = False
        with self.assertRaisesRegex(ValueError, "not URL references"):
            self.link()

    def test_native_pickup_source_requires_built_capability_and_binds_recipe_hash(self):
        source = self.source / "src/doom/pickup_sprites.c"
        source.parent.mkdir(parents=True)
        source.write_text("// synthetic compositor\n")
        recipe = self.source / "tools/doom_companion/local_assets.py"
        recipe.write_text("# synthetic asset recipe\n")
        with self.assertRaisesRegex(ValueError, "not been built"):
            self.link()
        binary = self.build / "bin/archipelago-doom.exe"
        binary.write_bytes(binary.read_bytes() + binding.NATIVE_PICKUP_MARKER)
        report = self.link()
        self.assertEqual(binding.NATIVE_PICKUP_PROFILE, report["pickup_sprite_profile"])
        self.assertEqual(digest(recipe), report["asset_recipe_sha256"])

    def test_prefix_from_a_different_source_bundle_is_rejected(self):
        path = self.dependency_build / "dependency-build.json"
        marker = json.loads(path.read_text())
        marker["bundle"] = str(self.root / "unrelated-source")
        path.write_text(json.dumps(marker))
        with self.assertRaisesRegex(ValueError, "dependency snapshot"):
            self.link()

    def test_cached_native_package_from_the_old_install_is_rejected(self):
        text = self.native_cache.read_text().replace(f"SDL2_DIR:PATH={self.prefix / 'lib/cmake/package'}",
                                                   f"SDL2_DIR:PATH={self.root / 'old-install'}")
        self.native_cache.write_text(text)
        with self.assertRaisesRegex(ValueError, "external package"):
            self.link()

    def test_dependency_built_from_other_source_is_rejected(self):
        self.dependency_cache.write_text(f"CMAKE_HOME_DIRECTORY:INTERNAL={self.root / 'old-source'}\n")
        with self.assertRaisesRegex(ValueError, "different preferred source"):
            self.link()

    def test_duplicate_cache_path_is_rejected(self):
        self.native_cache.write_text(self.native_cache.read_text() +
                                    f"CMAKE_HOME_DIRECTORY:INTERNAL={self.root / 'other-source'}\n")
        with self.assertRaisesRegex(ValueError, "ambiguous CMake"):
            self.link()

    def test_undeclared_runtime_dll_source_is_rejected(self):
        (self.prefix / "bin/unknown.dll").write_bytes(b"MZsynthetic library")
        def imports(path):
            return ["KERNEL32.dll"] if path.suffix == ".dll" else ["unknown.dll"]
        with self.assertRaisesRegex(ValueError, "source owner"):
            self.link(imports)

    def test_embedded_asset_table_is_rejected(self):
        (self.build / "embedded_files.h").write_text("#define NUM_EMBEDDED_FILES 1\n")
        with self.assertRaisesRegex(ValueError, "embedded resource"):
            self.link()

    def test_harness_source_requires_an_unambiguous_disabled_bool_option(self):
        header = self.source / "src/doom/apmine_gameplay_test.h"
        header.parent.mkdir(parents=True)
        header.write_text("/* synthetic harness fixture */\n")
        original = self.native_cache.read_text()
        for value in ("", "APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=ON\n",
                      "APMINE_GAMEPLAY_DIAGNOSTICS:STRING=OFF\n",
                      "APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=OFF\nAPMINE_GAMEPLAY_DIAGNOSTICS:BOOL=OFF\n"):
            with self.subTest(value=value):
                self.native_cache.write_text(original + value)
                with self.assertRaisesRegex(ValueError, "explicitly disabled"):
                    self.link()
        for value in ("OFF", "0", "FALSE", "NO"):
            with self.subTest(value=value):
                self.native_cache.write_text(original + f"APMINE_GAMEPLAY_DIAGNOSTICS:BOOL={value}\n")
                self.assertIs(self.link()["gameplay_diagnostics"], False)

    def test_diagnostic_binary_is_rejected_even_if_cache_says_off(self):
        self.native_cache.write_text(self.native_cache.read_text() + "APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=OFF\n")
        binary = self.build / "bin/archipelago-doom.exe"
        original = binary.read_bytes()
        for marker in binding.DIAGNOSTIC_MARKERS:
            with self.subTest(marker=marker):
                binary.write_bytes(original + marker)
                with self.assertRaisesRegex(ValueError, "diagnostic binary"):
                    self.link()


if __name__ == "__main__":
    unittest.main()
