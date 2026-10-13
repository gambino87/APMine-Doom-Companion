"""Stage exact local Doom dependency sources without binaries or publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


SYSTEM_DLLS = {
    "advapi32.dll", "avrt.dll", "bcrypt.dll", "cfgmgr32.dll", "comctl32.dll",
    "comdlg32.dll", "crypt32.dll", "dinput8.dll", "dwmapi.dll", "gdi32.dll",
    "hid.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "msvcp140.dll",
    "msvcp140_1.dll", "msvcp140_2.dll", "msvcrt.dll", "ole32.dll", "oleaut32.dll",
    "opengl32.dll", "powrprof.dll", "rpcrt4.dll", "secur32.dll", "setupapi.dll",
    "shell32.dll", "shlwapi.dll", "user32.dll", "usp10.dll", "uxtheme.dll",
    "version.dll", "vcruntime140.dll", "vcruntime140_1.dll", "winmm.dll",
    "winspool.drv", "ws2_32.dll",
}
NATIVE_SUFFIXES = {".exe", ".dll", ".lib", ".obj", ".o", ".a", ".so", ".dylib", ".pdb", ".jar", ".class", ".wasm", ".zip", ".sfc", ".smc", ".wad", ".pk3"}
BUILD_HELPERS = {"vcpkg-cmake", "vcpkg-cmake-config"}
COMPANION_BINARIES = {"archipelago-doom.exe", "apdoom-launcher.exe", "apdoom-setup.exe", "apcpp.dll"}


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def regular_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in source inputs: {path}")
        if path.is_file():
            yield path


def unused_upstream_prebuilt(package: str, relative: str) -> bool:
    if package == "sdl2" and relative == "android-project/gradle/wrapper/gradle-wrapper.jar":
        return True
    if package == "wavpack" and relative == "fuzzing/fuzzer_seed_corpus.zip":
        return True
    return package == "sdl2-mixer" and (
        relative.startswith("VisualC/external/optional/") and Path(relative).suffix.lower() in NATIVE_SUFFIXES
        or any(relative.startswith(f"Xcode/{platform}/SDL2.framework/")
               for platform in ("macOS", "iOS", "tvOS"))
    )


def native_header(path: Path) -> bool:
    with path.open("rb") as stream:
        magic = stream.read(4)
    return magic[:2] == b"MZ" or magic in {
        b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"IWAD", b"PWAD",
    }


def dumpbin_imports(path: Path, dumpbin: Path) -> list[str]:
    result = subprocess.run([str(dumpbin), "/dependents", str(path)], check=True,
                            capture_output=True, text=True, timeout=30)
    if "File Type:" not in result.stdout:
        raise ValueError(f"No PE dependency report for {path}")
    return sorted(set(re.findall(r"^\s+([\w.+-]+\.dll)\s*$", result.stdout, re.I | re.M)))


def runtime_closure(binary_dir: Path, prefix: Path, imports) -> dict:
    roots = [binary_dir / name for name in ("archipelago-doom.exe", "apdoom-launcher.exe", "apdoom-setup.exe")]
    lookup = {}
    for directory in (prefix / "bin", binary_dir):
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in (".exe", ".dll"):
                lookup[path.name.lower()] = path
    pending = list(roots)
    records = {}
    system = set()
    while pending:
        path = pending.pop()
        name = path.name.lower()
        if name in records:
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular runtime input: {path}")
        required = imports(path)
        records[name] = {"path": str(path), "sha256": digest(path), "imports": required}
        for dependency in required:
            key = dependency.lower()
            if key in lookup:
                pending.append(lookup[key])
            elif key in SYSTEM_DLLS or key.startswith(("api-ms-win-", "ext-ms-win-")):
                system.add(key)
            else:
                raise ValueError(f"Unresolved non-system DLL: {dependency} required by {path.name}")
    return {"binaries": records, "system_library_candidates": sorted(system)}


def package_catalog(prefix: Path) -> tuple[dict, dict]:
    packages = {}
    dlls = {}
    for spdx_path in sorted((prefix / "share").glob("*/vcpkg.spdx.json")):
        spdx = json.loads(spdx_path.read_text())
        port = next(package for package in spdx["packages"] if package["SPDXID"] == "SPDXRef-port")
        packages[port["name"]] = (spdx_path, spdx, port)
        for record in spdx["files"]:
            name = PurePosixPath(record["fileName"])
            if len(name.parts) == 2 and name.parts[0] == "bin" and name.suffix.lower() == ".dll":
                checksum = next(check["checksumValue"] for check in record["checksums"]
                                if check["algorithm"] == "SHA256")
                key = name.name.lower()
                if key in dlls:
                    raise ValueError(f"Ambiguous installed DLL owner: {key}")
                dlls[key] = (port["name"], checksum)
    return packages, dlls


def stage(binary_dir: Path, prefix: Path, buildtrees: Path, registry: Path,
          downloads: Path, vcpkg_root: Path, destination: Path, imports) -> dict:
    inputs = [path.resolve() for path in (binary_dir, prefix, buildtrees, registry, downloads, vcpkg_root)]
    binary_dir, prefix, buildtrees, registry, downloads, vcpkg_root = inputs
    destination = destination.resolve()
    repository = Path(__file__).resolve().parents[2]
    if destination.is_relative_to(repository) or any(
        destination.is_relative_to(root) or root.is_relative_to(destination) for root in inputs
    ):
        raise ValueError("Use a new private directory outside platform/source/build/cache inputs")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace dependency source bundle: {destination}")
    closure = runtime_closure(binary_dir, prefix, imports)
    catalog, dll_owners = package_catalog(prefix)
    required = set(BUILD_HELPERS)
    for name, record in closure["binaries"].items():
        path = Path(record["path"])
        if path.parent == binary_dir:
            if name not in COMPANION_BINARIES:
                raise ValueError(f"Unreviewed companion build artifact needs source ownership: {name}")
            continue
        if name not in dll_owners or record["sha256"] != dll_owners[name][1]:
            raise ValueError(f"Runtime DLL does not match installed package metadata: {name}")
        required.add(dll_owners[name][0])
    archives = {digest(path, "sha512"): path for path in downloads.iterdir()
                if path.is_file() and path.name.endswith((".tar.gz", ".tar.xz", ".zip"))}
    payloads = {}
    records = []
    omitted = []

    def add_tree(root: Path, target: str, *, source: bool = False):
        for path in regular_files(root):
            relative = path.relative_to(root).as_posix()
            if relative == ".vcpkg-extracted":
                continue
            if source and unused_upstream_prebuilt(target.removeprefix("sources/"), relative):
                omitted.append({"path": f"{target}/{relative}", "sha256": digest(path),
                                "reason": "Unused upstream binary build/test input; required libraries supplied as source"})
                continue
            if source and (path.suffix.lower() in NATIVE_SUFFIXES or native_header(path)):
                raise ValueError(f"Native/game payload in preferred source tree: {path}")
            payloads[f"{target}/{relative}"] = path

    for name in sorted(required):
        spdx_path, spdx, port = catalog[name]
        revision = port["downloadLocation"].rsplit("@", 1)[-1]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(f"Unpinned vcpkg port: {name}")
        port_root = registry / revision
        abi_path = buildtrees / name / "x64-windows.vcpkg_abi_info.txt"
        abi = dict(line.split(" ", 1) for line in abi_path.read_text().splitlines() if " " in line)
        for path in regular_files(port_root):
            if path.name in abi and re.fullmatch(r"[0-9a-f]{64}", abi[path.name]):
                if digest(path) != abi[path.name]:
                    raise ValueError(f"Port input differs from compiled package ABI: {name}/{path.name}")
        if not (port_root / "portfile.cmake").is_file():
            raise ValueError(f"Missing port recipe: {name}")
        add_tree(port_root, f"ports/{name}", source=True)
        payloads[f"metadata/{name}/vcpkg.spdx.json"] = spdx_path
        payloads[f"metadata/{name}/abi.txt"] = abi_path
        payloads[f"licenses/{name}.txt"] = spdx_path.parent / "copyright"
        resources = []
        for resource in spdx["packages"]:
            if not resource["SPDXID"].startswith("SPDXRef-resource-"):
                continue
            checks = [check["checksumValue"] for check in resource.get("checksums", [])
                      if check["algorithm"] == "SHA512"]
            archive = archives.get(checks[0]) if len(checks) == 1 else None
            if archive:
                # The actual patched preferred source is provided below. Do not
                # redistribute upstream tarballs containing unused opaque DLLs.
                resources.append({"name": resource["name"], "verified_archive": archive.name,
                                  "sha512": checks[0], "upstream": resource["downloadLocation"],
                                  "archive_retained_in_private_cache_only": True})
            elif name == "libpng" and resource["SPDXID"] == "SPDXRef-resource-1" and "apng" not in abi.get("features", "").split(";"):
                resources.append({"name": resource["name"], "not_used": "APNG feature disabled in recorded build"})
            else:
                raise ValueError(f"Missing exact dependency source archive: {name}/{resource['name']}")
        cache_path = buildtrees / name / "config-x64-windows-rel-CMakeCache.txt.log"
        source_path = None
        applied_patches = []
        if cache_path.is_file():
            cache = cache_path.read_text()
            match = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache, re.M)
            if not match:
                raise ValueError(f"No preferred source directory recorded for {name}")
            source_path = Path(match[1]).resolve()
            if not source_path.is_relative_to(buildtrees / name / "src"):
                raise ValueError(f"Source directory outside recorded package build tree: {name}")
            add_tree(source_path, f"sources/{name}", source=True)
            payloads[f"metadata/{name}/CMakeCache.txt"] = cache_path
            log_path = buildtrees / name / "stdout-x64-windows.log"
            if not log_path.is_file():
                raise ValueError(f"Missing source preparation log: {name}")
            applied_patches = re.findall(r"^-- Applying patch (.+)$", log_path.read_text(), re.M)
            for patch in applied_patches:
                if Path(patch).name != patch or not (port_root / patch).is_file():
                    raise ValueError(f"Unrecorded dependency patch: {name}/{patch}")
                if abi.get(patch) != digest(port_root / patch):
                    raise ValueError(f"Dependency patch does not match ABI: {name}/{patch}")
            payloads[f"metadata/{name}/source-preparation.log"] = log_path
        elif name not in BUILD_HELPERS:
            raise ValueError(f"Missing compiled dependency configuration: {name}")
        records.append({"name": name, "version": port["versionInfo"], "port_revision": revision,
                        "features": abi.get("features", ""), "resources": resources,
                        "applied_patches": applied_patches,
                        "preferred_source": f"sources/{name}" if source_path else f"ports/{name}"})
    add_tree(vcpkg_root / "scripts", "vcpkg/scripts", source=True)
    add_tree(vcpkg_root / "triplets", "vcpkg/triplets", source=True)
    for name in ("LICENSE.txt", "NOTICE.txt", "vcpkg-version.txt", "vcpkg-bundle.json"):
        payloads[f"vcpkg/{name}"] = vcpkg_root / name
    if len(payloads) > 50000 or sum(path.stat().st_size for path in payloads.values()) > 1024**3:
        raise ValueError("Dependency source bundle exceeds review bounds")
    manifest = {"schema_version": 1, "release_ready": False, "published": False,
                "status": "private-dependency-source-bundle", "runtime_closure": closure,
                "dependencies": records, "copied_inputs": [], "omitted_inputs": omitted,
                "unverified": ["independent dependency rebuild", "archive/patch source equivalence",
                               "publication and exact release source linkage", "historical source matching",
                               "system-library classification and redistribution review"]}
    for name, path in sorted(payloads.items()):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular source input: {path}")
        manifest["copied_inputs"].append({"path": name, "sha256": digest(path), "size": path.stat().st_size})
    destination.mkdir(parents=True)
    for entry in manifest["copied_inputs"]:
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payloads[entry["path"]], target)
        if digest(target) != entry["sha256"]:
            raise ValueError(f"Source changed during staging: {entry['path']}")
    (destination / "DEPENDENCY_SOURCES.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify(destination)
    return manifest


def verify(root: Path) -> dict:
    root = root.resolve()
    manifest = json.loads((root / "DEPENDENCY_SOURCES.json").read_text())
    if manifest.get("status") != "private-dependency-source-bundle" or manifest.get("release_ready") is not False:
        raise ValueError("Not a private dependency source bundle")
    expected = {"DEPENDENCY_SOURCES.json"}
    for entry in manifest["copied_inputs"]:
        name = entry["path"]
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or ":" in name or "\\" in name:
            raise ValueError(f"Unsafe source member: {name}")
        if name in expected:
            raise ValueError(f"Duplicate source member: {name}")
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing regular source member: {name}")
        if name.startswith(("sources/", "ports/", "vcpkg/")) and (
            path.suffix.lower() in NATIVE_SUFFIXES or native_header(path)
        ):
            raise ValueError(f"Native/game payload in source bundle: {name}")
        if digest(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
            raise ValueError(f"Dependency source changed: {name}")
        expected.add(name)
    actual = {path.relative_to(root).as_posix() for path in regular_files(root)}
    if actual != expected:
        raise ValueError(f"Unexpected source bundle members: {sorted(actual ^ expected)}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    for name in ("binary-dir", "library-prefix", "buildtrees", "registry-trees", "downloads", "vcpkg-root", "destination", "dumpbin"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    if args.verify:
        if any(value for name, value in vars(args).items() if name != "verify"):
            parser.error("--verify cannot be combined with staging arguments")
        manifest = verify(args.verify)
    else:
        if not all(value for name, value in vars(args).items() if name != "verify"):
            parser.error("Staging requires all eight source/tool paths")
        manifest = stage(args.binary_dir, args.library_prefix, args.buildtrees, args.registry_trees,
                         args.downloads, args.vcpkg_root, args.destination,
                         lambda path: dumpbin_imports(path, args.dumpbin))
    print(f"{'Verified' if args.verify else 'Staged'} {len(manifest['dependencies'])} dependencies and {len(manifest['copied_inputs'])} inputs.")
    print("Private preparation only; rebuild, source equivalence and release review remain required.")


if __name__ == "__main__":
    main()
