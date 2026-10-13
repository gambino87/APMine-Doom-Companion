import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tarfile
import unittest
from unittest.mock import patch

from tools.doom_companion import dependency_sources as sources
from tools.doom_companion.prove_dependency_sources import prove


class DoomDependencySourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "binaries"
        self.prefix = self.root / "installed"
        self.buildtrees = self.root / "buildtrees"
        self.registry = self.root / "registry"
        self.downloads = self.root / "downloads"
        self.vcpkg = self.root / "vcpkg"
        self.output = self.root / "private-source-output"
        for root in (self.binary, self.prefix / "bin", self.downloads, self.vcpkg):
            root.mkdir(parents=True)
        for name in ("archipelago-doom.exe", "apdoom-launcher.exe", "apdoom-setup.exe"):
            (self.binary / name).write_bytes(b"MZsynthetic test executable")
        self.dll = self.prefix / "bin/example.dll"
        self.dll.write_bytes(b"MZsynthetic test DLL")
        self.sha = hashlib.sha256(self.dll.read_bytes()).hexdigest()
        self.port = self.registry / ("a" * 40)
        self.port.mkdir(parents=True)
        (self.port / "portfile.cmake").write_text("# synthetic source recipe\n")
        self.source = self.buildtrees / "example/src/preferred.clean"
        self.source.mkdir(parents=True)
        (self.source / "source.c").write_text("/* synthetic library source */\n")
        self.archive = self.downloads / "example.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            raw = b"/* synthetic library source */\n"
            member = tarfile.TarInfo("example-1.0/source.c")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        self.share = self.prefix / "share/example"
        self.share.mkdir(parents=True)
        (self.share / "copyright").write_text("Synthetic fixture notice\n")
        self.spdx = {
            "packages": [
                {"SPDXID": "SPDXRef-port", "name": "example", "versionInfo": "1.0",
                 "downloadLocation": "git+https://example.invalid/vcpkg@" + "a" * 40},
                {"SPDXID": "SPDXRef-resource-0", "name": "source/example",
                 "downloadLocation": "https://example.invalid/source.tar.gz",
                 "checksums": [{"algorithm": "SHA512", "checksumValue": sources.digest(self.archive, "sha512")}]},
            ],
            "files": [{"fileName": "./bin/example.dll",
                       "checksums": [{"algorithm": "SHA256", "checksumValue": self.sha}]}],
        }
        (self.share / "vcpkg.spdx.json").write_text(json.dumps(self.spdx))
        (self.buildtrees / "example/x64-windows.vcpkg_abi_info.txt").write_text(
            f"portfile.cmake {sources.digest(self.port / 'portfile.cmake')}\nfeatures core\n")
        (self.buildtrees / "example/config-x64-windows-rel-CMakeCache.txt.log").write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={self.source}\n")
        (self.buildtrees / "example/stdout-x64-windows.log").write_text("Synthetic source preparation\n")
        for name in ("scripts/ports.cmake", "triplets/x64-windows.cmake", "LICENSE.txt", "NOTICE.txt",
                     "vcpkg-version.txt", "vcpkg-bundle.json"):
            path = self.vcpkg / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic build input\n")

    def imports(self, path):
        return ["KERNEL32.dll"] if path.suffix == ".dll" else ["example.dll", "api-ms-win-crt-runtime-l1-1-0.dll"]

    def stage(self):
        with patch.object(sources, "BUILD_HELPERS", set()):
            return sources.stage(self.binary, self.prefix, self.buildtrees, self.registry, self.downloads,
                                 self.vcpkg, self.output, self.imports)

    def test_sources_and_recipes_are_provided_without_native_or_raw_archive_payloads(self):
        report = self.stage()
        self.assertEqual(4, len(report["runtime_closure"]["binaries"]))
        self.assertEqual(["example"], [entry["name"] for entry in report["dependencies"]])
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["published"])
        self.assertEqual(self.source.joinpath("source.c").read_bytes(),
                         self.output.joinpath("sources/example/source.c").read_bytes())
        self.assertFalse(any(path.suffix in sources.NATIVE_SUFFIXES for path in self.output.rglob("*")))
        self.assertFalse((self.output / "archives").exists())
        self.assertEqual(report, sources.verify(self.output))

    def test_unknown_runtime_dependency_is_not_treated_as_a_system_library(self):
        with self.assertRaisesRegex(ValueError, "Unresolved non-system DLL"):
            sources.runtime_closure(self.binary, self.prefix, lambda path: ["unreviewed.dll"])

    def test_preferred_source_is_compared_to_pinned_upstream_archive(self):
        self.stage()
        report = prove(self.output, self.downloads)
        self.assertEqual(1, len(report["dependencies"]))
        self.assertTrue(report["dependencies"][0]["source_match"])
        self.assertFalse(report["release_ready"])

    def test_unreviewed_archive_link_is_rejected(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            member = tarfile.TarInfo("example-1.0/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        self.spdx["packages"][1]["checksums"][0]["checksumValue"] = sources.digest(self.archive, "sha512")
        (self.share / "vcpkg.spdx.json").write_text(json.dumps(self.spdx))
        self.stage()
        with self.assertRaisesRegex(ValueError, "Non-regular source archive"):
            prove(self.output, self.downloads)

    def test_changed_dll_fails_before_creating_output(self):
        self.dll.write_bytes(b"changed DLL")
        with self.assertRaisesRegex(ValueError, "package metadata"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_extra_dll_in_companion_directory_needs_its_own_source_review(self):
        (self.binary / "unreviewed.dll").write_bytes(b"MZsynthetic")
        def imports(path):
            return ["KERNEL32.dll"] if path.suffix == ".dll" else ["unreviewed.dll"]
        with patch.object(sources, "BUILD_HELPERS", set()), self.assertRaisesRegex(ValueError, "source ownership"):
            sources.stage(self.binary, self.prefix, self.buildtrees, self.registry, self.downloads,
                          self.vcpkg, self.output, imports)
        self.assertFalse(self.output.exists())

    def test_changed_recipe_fails_before_creating_output(self):
        (self.port / "portfile.cmake").write_text("changed recipe")
        with self.assertRaisesRegex(ValueError, "compiled package ABI"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_missing_archive_fails_before_creating_output(self):
        self.archive.unlink()
        with self.assertRaisesRegex(ValueError, "exact dependency source archive"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_native_payload_in_preferred_source_is_rejected(self):
        (self.source / "original-game.wad").write_bytes(b"PWADsynthetic")
        with self.assertRaisesRegex(ValueError, "Native/game payload"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_renamed_native_payload_is_rejected_by_its_header(self):
        (self.source / "no-extension").write_bytes(b"\xcf\xfa\xed\xfesynthetic Mach-O")
        with self.assertRaisesRegex(ValueError, "Native/game payload"):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_prebuilt_exclusions_are_scoped_to_reviewed_unused_paths(self):
        self.assertTrue(sources.unused_upstream_prebuilt("sdl2", "android-project/gradle/wrapper/gradle-wrapper.jar"))
        self.assertTrue(sources.unused_upstream_prebuilt("wavpack", "fuzzing/fuzzer_seed_corpus.zip"))
        self.assertTrue(sources.unused_upstream_prebuilt("sdl2-mixer", "Xcode/macOS/SDL2.framework/Versions/A/SDL2"))
        self.assertFalse(sources.unused_upstream_prebuilt("sdl2", "important-runtime.jar"))
        self.assertFalse(sources.unused_upstream_prebuilt("unreviewed", "Xcode/macOS/SDL2.framework/Versions/A/SDL2"))

    def test_existing_destination_is_never_overwritten(self):
        self.output.mkdir()
        (self.output / "keep").write_text("existing")
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual("existing", (self.output / "keep").read_text())

    def test_verifier_rejects_changed_or_unexpected_source_members(self):
        self.stage()
        member = self.output / "sources/example/source.c"
        original = member.read_bytes()
        member.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "source changed"):
            sources.verify(self.output)
        member.write_bytes(original)
        (self.output / "opaque.dll").write_bytes(b"MZsynthetic")
        with self.assertRaisesRegex(ValueError, "Unexpected source"):
            sources.verify(self.output)

    def test_verifier_rejects_manifest_path_traversal(self):
        self.stage()
        path = self.output / "DEPENDENCY_SOURCES.json"
        manifest = json.loads(path.read_text())
        manifest["copied_inputs"][0]["path"] = "../outside.c"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe source member"):
            sources.verify(self.output)

    def test_verifier_does_not_trust_a_native_payload_added_to_the_manifest(self):
        self.stage()
        native = self.output / "sources/example/renamed-data"
        native.write_bytes(b"MZsynthetic compiled payload")
        path = self.output / "DEPENDENCY_SOURCES.json"
        manifest = json.loads(path.read_text())
        manifest["copied_inputs"].append({"path": "sources/example/renamed-data",
                                         "sha256": sources.digest(native), "size": native.stat().st_size})
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Native/game payload"):
            sources.verify(self.output)


if __name__ == "__main__":
    unittest.main()
