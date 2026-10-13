"""Bind privately built Doom binaries to an assembled preferred-source snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .dependency_sources import COMPANION_BINARIES, digest, dumpbin_imports, runtime_closure, verify
from .inventory import DIAGNOSTIC_MARKERS, GAMEPLAY_DIAGNOSTIC_MARKER, embedded_zips
from .source_repository import verify_source_snapshot


def cache_path(cache: Path, key: str) -> Path:
    values = re.findall(rf"^{re.escape(key)}:[A-Z]+=(.+)$", cache.read_text(), re.M)
    if len(values) != 1 or values[0].endswith("-NOTFOUND"):
        raise ValueError(f"Missing or ambiguous CMake path: {key} in {cache}")
    return Path(values[0]).resolve()


def link(source: Path, build: Path, prefix: Path, imports) -> dict:
    source, build, prefix = source.resolve(), build.resolve(), prefix.resolve()
    snapshot = verify_source_snapshot(source)
    if not snapshot.get("required_dependency_sources_present"):
        raise ValueError("Assembled preferred dependency source is required, not URL references")
    vendor = source / "vendor/native-dependencies"
    dependencies = verify(vendor)
    source_hash = digest(source / "SOURCE_SNAPSHOT.json")
    dependency_hash = digest(vendor / "DEPENDENCY_SOURCES.json")
    marker = json.loads((prefix.parent / "dependency-build.json").read_text(encoding="utf-8-sig"))
    if Path(marker["bundle"]).resolve() != vendor or marker["snapshot_sha256"].lower() != dependency_hash:
        raise ValueError("Installed prefix was not built from this candidate's dependency snapshot")
    native_cache = build / "CMakeCache.txt"
    if cache_path(native_cache, "CMAKE_HOME_DIRECTORY") != source:
        raise ValueError("Native build uses a different engine source checkout")
    if cache_path(native_cache, "CMAKE_PREFIX_PATH") != prefix:
        raise ValueError("Native build uses a different dependency prefix")
    diagnostics = re.findall(r"^APMINE_GAMEPLAY_DIAGNOSTICS:([A-Z]+)=(.*)$", native_cache.read_text(), re.M)
    has_harness = (source / "src/doom/apmine_gameplay_test.h").is_file()
    if diagnostics or has_harness:
        if (len(diagnostics) != 1 or diagnostics[0][0] != "BOOL"
                or diagnostics[0][1].upper() not in {"0", "OFF", "FALSE", "NO"}):
            raise ValueError("Normal-client source linkage requires gameplay diagnostics explicitly disabled")
    for key in ("SDL2_DIR", "SDL2_mixer_DIR", "PNG_DIR", "SampleRate_DIR", "ZLIB_DIR"):
        if not cache_path(native_cache, key).is_relative_to(prefix):
            raise ValueError(f"Native build resolves an external package: {key}")
    header = (build / "embedded_files.h").read_text()
    if "#define NUM_EMBEDDED_FILES 0" not in header or "EMBEDDED_FILE_BASEASSETS_ZIP" in header:
        raise ValueError("Native build contains an unreviewed embedded resource table")
    owners = {}
    for dependency in dependencies["dependencies"]:
        name = dependency["name"]
        if not dependency["preferred_source"].startswith("sources/"):
            continue
        cache = prefix.parent / name / "CMakeCache.txt"
        if cache_path(cache, "CMAKE_HOME_DIRECTORY") != (vendor / dependency["preferred_source"]).resolve():
            raise ValueError(f"Dependency build uses different preferred source: {name}")
        if cache_path(cache, "CMAKE_INSTALL_PREFIX") != prefix:
            raise ValueError(f"Dependency installs to a different prefix: {name}")
        spdx = json.loads((vendor / "metadata" / name / "vcpkg.spdx.json").read_text())
        for file in spdx["files"]:
            path = Path(file["fileName"])
            if path.parent.as_posix() in ("bin", "./bin") and path.suffix.lower() == ".dll":
                key = path.name.lower()
                if key in owners:
                    raise ValueError(f"Ambiguous DLL source owner: {key}")
                owners[key] = name
    closure = runtime_closure(build / "bin", prefix, imports)
    binaries = []
    for name, binary in sorted(closure["binaries"].items()):
        path = Path(binary["path"])
        if path.parent == build / "bin" and name in COMPANION_BINARIES:
            owner = "engine/APCpp"
            source_path = "."
        elif path.parent == prefix / "bin" and name in owners:
            owner = owners[name]
            source_path = f"vendor/native-dependencies/sources/{owner}"
        else:
            raise ValueError(f"Runtime binary has no reviewed preferred source owner: {name}")
        raw = path.read_bytes()
        if list(embedded_zips(raw)):
            raise ValueError(f"Runtime binary embeds an unreviewed ZIP payload: {name}")
        if any(marker in raw for marker in DIAGNOSTIC_MARKERS):
            raise ValueError(f"Normal-client linkage rejects a gameplay diagnostic binary: {name}")
        binaries.append({"name": path.name, "sha256": binary["sha256"], "source_component": owner,
                         "preferred_source": source_path, "imports": binary["imports"]})
    return {"schema_version": 1, "release_ready": False, "published": False,
            "status": "private-binary-source-link", "source_snapshot_sha256": source_hash,
            "gameplay_diagnostics": False,
            "dependency_snapshot_sha256": dependency_hash, "preferred_source_inputs": len(snapshot["copied_inputs"]),
            "binaries": binaries, "system_library_candidates": closure["system_library_candidates"],
            "build_recipes": {name: digest(source / "tools/doom_companion" / name)
                              for name in ("build_local.ps1", "build_dependencies.ps1")},
            "source_access": {"url": None, "immutable_revision": None},
            "limitations": ["Technical local build linkage, not cryptographic build attestation",
                            "No public source delivery or historical binary/source matching",
                            "System-library redistribution/provenance and remaining acceptance review"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "build", "library-prefix", "dumpbin", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffix != ".json" or output.exists():
        raise ValueError("Use a new JSON build record; existing records are never replaced")
    if output.is_relative_to(args.source.resolve()) and not output.is_relative_to(args.build.resolve()):
        raise ValueError("Keep generated binary linkage outside preferred source inputs")
    report = link(args.source, args.build, args.library_prefix, lambda path: dumpbin_imports(path, args.dumpbin))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"Linked {len(report['binaries'])} runtime binaries to {report['preferred_source_inputs']} source inputs.")
    print("Private record only; no release, source URL or publication was created.")


if __name__ == "__main__":
    main()
