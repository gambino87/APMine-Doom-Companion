"""Stage a private, source-only Doom companion repository candidate."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import shutil

from .inventory import sha256
from .prepare import NOTICE_NAMES, TEXT_SUFFIXES
from .dependency_sources import verify as verify_dependency_sources


GENERATED_RESOURCES = {f"src/{name}-res.rc" for name in ("doom", "heretic", "hexen", "strife", "setup", "launcher")}
GENERATED_RESOURCES.add("src/setup/setup-manifest.xml")
ICONS = {f"src/{name}/{name}_icon.c" for name in ("doom", "heretic", "hexen", "strife", "setup")}
DEPENDENCIES = {
    "sdl2", "sdl2-mixer", "sdl2-net", "zlib", "libpng", "libsamplerate",
    "libogg", "libvorbis", "wavpack", "fluidsynth", "gcem", "vcpkg-cmake", "vcpkg-cmake-config",
}
HELPERS = ("__init__.py", "inventory.py", "prepare.py", "local_assets.py", "bootstrap.py", "build_local.ps1", "source_repository.py",
           "dependency_sources.py", "prove_dependency_sources.py", "build_dependencies.ps1", "release_source_link.py",
           "ln_handshake.h", "apmine_gameplay_test.h", "apmine_network_test.h", "practice_items.h", "gameplay.py",
           "network_gameplay.py", "source_access.py")
OMIT_DIRECTORIES = {".git", "__pycache__", ".github", ".devcontainer", "data", "quickcheck", "pkg", "mbedtls-3.4.0"}
OMIT_FILES = {"prepare-manifest.json", ".travis.sh", "create_release.bat", "appimage.sh"}
OWNED_NATIVE_ADDITIONS = {"src/launcher/ln_handshake.h", "src/doom/apmine_gameplay_test.h", "src/doom/apmine_network_test.h",
                          "src/archipelago/practice_items.h"}
MODIFICATION_NOTICE = re.compile(
    r"^(?:#|//|/\*|<!--) APMine companion change, (\d{4}-\d{2}-\d{2})[.:]", re.MULTILINE)


def is_text_source(name: str) -> bool:
    path = PurePosixPath(name)
    return path.name in NOTICE_NAMES | {"CMakeLists.txt"} or path.suffix.lower() in TEXT_SUFFIXES


def text_source_bytes(root: Path, name: str) -> bytes:
    raw = source_file(root, name).read_bytes()
    if b"\0" in raw:
        raise ValueError(f"Non-text source input requires review: {name}")
    raw.decode("utf-8")
    return raw


def modification_dates(raw: bytes, name: str) -> list[str]:
    dates = sorted(set(MODIFICATION_NOTICE.findall(raw.decode("utf-8")[:4096])))
    if not dates:
        raise ValueError(f"Missing prominent dated companion modification notice: {name}")
    for value in dates:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"Invalid companion modification date in {name}: {value}") from error
    return dates


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or "\\" in value or ":" in value or path.is_absolute() or any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f"Unsafe source input path: {value}")
    return path


def source_file(root: Path, name: str) -> Path:
    path = root / relative_path(name)
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
        raise ValueError(f"Source inputs must not follow symlinks: {name}")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or escaping source input: {name}")
    return path


def validate_prepared_source(root: Path) -> None:
    for name in ("COPYING.md", "APCpp/LICENSE", "APCpp/IXWebSocket/LICENSE.txt", "APCpp/jsoncpp/LICENSE"):
        source_file(root, name).read_text(encoding="utf-8")
    for name in ("CMakeLists.txt", "src/CMakeLists.txt"):
        text = source_file(root, name).read_text(encoding="utf-8")
        if any(token in text for token in ("APDOOM_PACKAGE_SCRIPT", "APDOOM_WEB_DOWNLOAD_DIR", "package-apdoom-launcher")):
            raise ValueError("Remove historical platform publication hooks before source staging")
    if "APMINE_EXTERNAL_ASSETS_ONLY=1" not in source_file(root, "CMakeLists.txt").read_text(encoding="utf-8"):
        raise ValueError("Only the separately prepared, external-assets companion can be staged")
    embed = source_file(root, "embed/CMakeLists.txt").read_text(encoding="utf-8")
    if "#define NUM_EMBEDDED_FILES 0" not in embed or "message(FATAL_ERROR" not in embed:
        raise ValueError("Source staging requires the disabled asset embedding build")
    for archive in (root / "embed").iterdir():
        if archive.suffix.lower() in (".zip", ".apworld", ".wad", ".pk3"):
            raise ValueError(f"Remove embedded asset/world archives before staging: {archive.name}")
    for name in ICONS:
        icon = source_file(root, name).read_text(encoding="utf-8")
        if len(icon) > 512 or not re.search(r"icon_data\[\]\s*=\s*\{0\}", icon):
            raise ValueError(f"Unreviewed generated icon pixel array: {name}")
    for name in sorted(GENERATED_RESOURCES):
        if name.endswith(".rc"):
            resource = source_file(root, name + ".in").read_text(encoding="utf-8")
            if re.search(r"^\s*\d+\s+ICON\s+", resource, re.MULTILINE):
                raise ValueError(f"Remove unreviewed program Windows icons before staging: {name}.in")


def dependency_notices(prefix: Path) -> tuple[dict[str, bytes], list[dict]]:
    files = {}
    records = []
    for name in sorted(DEPENDENCIES):
        directory = prefix / "share" / name
        if not directory.exists():
            continue
        notice = source_file(prefix, f"share/{name}/copyright").read_bytes()
        notice.decode("utf-8")
        spdx = json.loads(source_file(prefix, f"share/{name}/vcpkg.spdx.json").read_text(encoding="utf-8"))
        packages = [
            {key: package[key] for key in ("SPDXID", "name", "versionInfo", "downloadLocation", "licenseDeclared") if key in package}
            for package in spdx["packages"]
        ]
        files[f"licenses/dependencies/{name}.txt"] = notice
        records.append({
            "name": name, "packages": packages, "notice_sha256": sha256(notice),
            "source_delivery": "not-included; references are not corresponding-source delivery",
            "binary_inclusion": "installed build dependency; linkage must be checked per release",
        })
    if not records:
        raise ValueError("No dependency notices/SPDX build records found in the supplied library prefix")
    return files, records


def stage_repository(source_root: Path, destination: Path, library_prefix: Path,
                     dependency_sources: Path | None = None) -> dict:
    source_root = source_root.resolve()
    destination = destination.resolve()
    library_prefix = library_prefix.resolve()
    if dependency_sources is not None:
        dependency_sources = dependency_sources.resolve()
    repository = Path(__file__).resolve().parents[2]
    if destination.is_relative_to(repository) or any(
        destination.is_relative_to(root) or root.is_relative_to(destination)
        for root in (source_root, library_prefix, *([dependency_sources] if dependency_sources else []))
    ):
        raise ValueError("Use a new, separate source candidate directory outside the platform and companion trees")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace an existing source candidate: {destination}")
    validate_prepared_source(source_root)
    prepared = json.loads(source_file(source_root, "prepare-manifest.json").read_text(encoding="utf-8"))
    if prepared.get("status") != "private-source-working-copy":
        raise ValueError("A separately prepared companion source manifest is required")
    payloads = {}
    changes = []
    omitted = []
    for entry in prepared["copied_inputs"]:
        name = str(relative_path(entry["path"]))
        if name != "APCpp/mbedtls-3.4.0.tar.gz" and not is_text_source(name):
            raise ValueError(f"Unreviewed source/asset input: {name}")
        # Do not carry inherited CI/publication hooks or duplicate extracted TLS
        # sources into a new repository. The reviewed TLS source tar is retained.
        if name in GENERATED_RESOURCES | OMIT_FILES or any(part in OMIT_DIRECTORIES for part in PurePosixPath(name).parts[:-1]):
            omitted.append(name)
            continue
        if name in payloads:
            raise ValueError(f"Duplicate source input: {name}")
        raw = source_file(source_root, name).read_bytes()
        if name == "APCpp/mbedtls-3.4.0.tar.gz":
            if sha256(raw) != entry["sha256"]:
                raise ValueError("The retained TLS source archive changed and needs separate review")
        else:
            raw = text_source_bytes(source_root, name)
        payloads[name] = raw
        if sha256(raw) != entry["sha256"]:
            if PurePosixPath(name).name in NOTICE_NAMES:
                raise ValueError(f"Upstream copyright/license notice changed since recovery: {name}")
            changes.append({"path": name, "before_sha256": entry["sha256"], "after_sha256": sha256(raw),
                            "modification_dates": modification_dates(raw, name)})
    # A private prototype can gain required source after preparation (for
    # example JsonCpp's .inl). Inventory those additions instead of silently
    # exporting an incomplete build merely because the old manifest omitted them.
    added = []
    stack = [source_root]
    while stack:
        directory = stack.pop()
        for path in sorted(directory.iterdir()):
            name = path.relative_to(source_root).as_posix()
            if path.is_symlink():
                raise ValueError(f"Symlink in prepared source: {name}")
            if path.is_dir():
                if path.name not in OMIT_DIRECTORIES and not path.name.startswith(("build", ".venv")):
                    stack.append(path)
            elif name not in payloads and name not in GENERATED_RESOURCES | OMIT_FILES and is_text_source(name):
                raw = text_source_bytes(source_root, name)
                payloads[name] = raw
                added.append({"path": name, "sha256": sha256(raw)})
    for name in ICONS:
        payloads[name] = source_file(source_root, name).read_bytes()
    payloads["COPYING.GPL3.txt"] = source_file(source_root, "APCpp/LICENSE").read_bytes()
    payloads["README.upstream.md"] = payloads.pop("README.md")
    helper_root = Path(__file__).resolve().parent
    for name in HELPERS:
        payloads[f"tools/doom_companion/{name}"] = (helper_root / name).read_bytes()
    asset_tests = repository / "test/tools/test_doom_companion_local_assets.py"
    if not asset_tests.is_file():
        asset_tests = repository / "tests/test_local_assets.py"
    payloads["tests/test_local_assets.py"] = asset_tests.read_bytes()
    dependency_tests = repository / "test/tools/test_doom_companion_dependency_sources.py"
    if not dependency_tests.is_file():
        dependency_tests = repository / "tests/test_dependency_sources.py"
    payloads["tests/test_dependency_sources.py"] = dependency_tests.read_bytes()
    bootstrap_tests = repository / "test/tools/test_doom_companion_bootstrap.py"
    if not bootstrap_tests.is_file():
        bootstrap_tests = repository / "tests/test_bootstrap.py"
    payloads["tests/test_bootstrap.py"] = bootstrap_tests.read_bytes()
    link_tests = repository / "test/tools/test_doom_companion_release_source_link.py"
    if not link_tests.is_file():
        link_tests = repository / "tests/test_release_source_link.py"
    payloads["tests/test_release_source_link.py"] = link_tests.read_bytes()
    handshake_tests = repository / "test/tools/test_doom_companion_handshake.py"
    if not handshake_tests.is_file():
        handshake_tests = repository / "tests/test_handshake.py"
    payloads["tests/test_handshake.py"] = handshake_tests.read_bytes()
    for name in ("gameplay", "practice_items", "network_gameplay", "source_access"):
        tests = repository / f"test/tools/test_doom_companion_{name}.py"
        if not tests.is_file():
            tests = repository / f"tests/test_{name}.py"
        payloads[f"tests/test_{name}.py"] = tests.read_bytes()
    source_tests = repository / "test/tools/test_doom_companion_source_repository.py"
    if not source_tests.is_file():
        source_tests = repository / "tests/test_source_repository.py"
    payloads["tests/test_source_repository.py"] = source_tests.read_bytes()
    native_handshake = payloads.get("src/launcher/ln_handshake.h")
    if native_handshake is not None and native_handshake != payloads["tools/doom_companion/ln_handshake.h"]:
        raise ValueError("Native launcher handshake header differs from the protocol-test input")
    for native_path, helper in (("src/doom/apmine_gameplay_test.h", "apmine_gameplay_test.h"),
                                ("src/doom/apmine_network_test.h", "apmine_network_test.h"),
                                ("src/archipelago/practice_items.h", "practice_items.h")):
        native = payloads.get(native_path)
        if native is not None and native != payloads[f"tools/doom_companion/{helper}"]:
            raise ValueError(f"Native {helper} differs from the acceptance-test input")
    for entry in added:
        if entry["path"] in OWNED_NATIVE_ADDITIONS:
            entry["modification_dates"] = modification_dates(payloads[entry["path"]], entry["path"])
    templates = helper_root / "source_templates"
    for name in ("README.md", "BUILDING.md", "THIRD_PARTY_NOTICES.md", ".gitignore"):
        payloads[name] = (templates / name).read_bytes()
        payloads[f"tools/doom_companion/source_templates/{name}"] = payloads[name]
    notices, dependencies = dependency_notices(library_prefix)
    payloads.update(notices)
    bundle = None
    if dependency_sources is not None:
        bundle = verify_dependency_sources(dependency_sources)
        for entry in bundle["copied_inputs"]:
            name = str(relative_path(entry["path"]))
            raw = source_file(dependency_sources, name).read_bytes()
            if sha256(raw) != entry["sha256"]:
                raise ValueError(f"Dependency source changed during assembly: {name}")
            payloads[f"vendor/native-dependencies/{name}"] = raw
        payloads["vendor/native-dependencies/DEPENDENCY_SOURCES.json"] = source_file(
            dependency_sources, "DEPENDENCY_SOURCES.json").read_bytes()
        required = {entry["name"] for entry in bundle["dependencies"]}
        supplied = {entry["name"] for entry in dependencies}
        if not required <= supplied:
            raise ValueError(f"Missing corresponding dependency notices: {sorted(required - supplied)}")
        for entry in dependencies:
            if entry["name"] in required:
                notice = payloads[f"vendor/native-dependencies/licenses/{entry['name']}.txt"]
                if sha256(notice) != entry["notice_sha256"]:
                    raise ValueError(f"Dependency notice version differs from source: {entry['name']}")
                entry["preferred_source"] = next(record["preferred_source"] for record in bundle["dependencies"]
                                                  if record["name"] == entry["name"])
                entry["source_delivery"] = "present in private candidate; not publicly delivered"
            else:
                entry["source_delivery"] = "not linked by the captured build profile; notice/reference retained"
    payloads["DEPENDENCIES.json"] = (json.dumps({"schema_version": 1, "source_delivery_complete": False,
                                             "required_dependency_sources_present": bundle is not None,
                                             "installed_dependencies": dependencies}, indent=2) + "\n").encode()
    manifest = {
        "schema_version": 1, "release_ready": False, "status": "private-source-repository-candidate",
        "git_repository_created": False, "published": False,
        "source_match": "prepared prototype inputs; not verified corresponding source for retired binaries",
        "modification_notice_profile": 1,
        "required_dependency_sources_present": bundle is not None,
        "copied_inputs": [{"path": name, "sha256": sha256(raw)} for name, raw in sorted(payloads.items())],
        "changes_from_prepared_baseline": changes, "omitted_generated_or_maintenance_inputs": sorted(omitted),
        "source_additions_since_preparation": sorted(added, key=lambda entry: entry["path"]),
        "remaining": (["Native dependency source/build recovery"] if bundle is None else []) +
                     ["Clean build and binary/source linkage from this assembled candidate", "Live AP gameplay verification",
                      "Historical binary/source matching and recipient source access", "Source/art provenance review",
                      "Explicit publication authorization; no public source URL exists yet"],
    }
    destination.mkdir(parents=True)
    for name, raw in sorted(payloads.items()):
        target = destination / relative_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
    with (destination / "SOURCE_SNAPSHOT.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    verify_source_snapshot(destination)
    return manifest


def verify_source_snapshot(root: Path) -> dict:
    root = root.resolve()
    manifest = json.loads(source_file(root, "SOURCE_SNAPSHOT.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "private-source-repository-candidate" or manifest.get("release_ready") is not False:
        raise ValueError("Source candidate status is invalid; this check does not grant release clearance")
    profile = manifest.get("modification_notice_profile")
    if type(profile) is not int or profile != 1:
        raise ValueError("Source candidate lacks the reviewed dated-modification notice profile")
    names = set()
    for entry in manifest["copied_inputs"]:
        name = str(relative_path(entry["path"]))
        if name in names:
            raise ValueError(f"Duplicate source snapshot entry: {name}")
        names.add(name)
        if sha256(source_file(root, name).read_bytes()) != entry["sha256"]:
            raise ValueError(f"Source snapshot hash changed: {name}")
    changed_names = set()
    for entry in manifest["changes_from_prepared_baseline"]:
        name = str(relative_path(entry["path"]))
        if PurePosixPath(name).name in NOTICE_NAMES:
            raise ValueError(f"Upstream copyright/license notice cannot be a modified input: {name}")
        staged = "README.upstream.md" if name == "README.md" else name
        if staged not in names or name in changed_names:
            raise ValueError(f"Missing or duplicate modified source inventory entry: {name}")
        changed_names.add(name)
        raw = source_file(root, staged).read_bytes()
        if sha256(raw) != entry["after_sha256"] or modification_dates(raw, name) != entry.get("modification_dates"):
            raise ValueError(f"Companion modification notice/hash record differs: {name}")
    added_names = set()
    for entry in manifest["source_additions_since_preparation"]:
        name = str(relative_path(entry["path"]))
        if name not in names or name in added_names:
            raise ValueError(f"Missing or duplicate added source inventory entry: {name}")
        added_names.add(name)
        raw = source_file(root, name).read_bytes()
        if sha256(raw) != entry["sha256"]:
            raise ValueError(f"Companion addition hash record differs: {name}")
        if name in OWNED_NATIVE_ADDITIONS:
            if modification_dates(raw, name) != entry.get("modification_dates"):
                raise ValueError(f"Companion addition notice record differs: {name}")
    unrecorded = (names & OWNED_NATIVE_ADDITIONS) - added_names - changed_names
    if unrecorded:
        raise ValueError(f"Missing companion native notice records: {sorted(unrecorded)}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ((len(relative.parts) > 1 and (relative.parts[0] == ".git" or relative.parts[0].startswith("build")))
                or "__pycache__" in relative.parts[:-1]):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink in source candidate: {relative}")
        if path.is_file() and relative.as_posix() not in names | GENERATED_RESOURCES | {"SOURCE_SNAPSHOT.json"}:
            raise ValueError(f"Unreviewed extra source candidate input: {relative}")
    validate_prepared_source(root)
    if manifest.get("required_dependency_sources_present"):
        verify_dependency_sources(root / "vendor/native-dependencies")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="Verify an existing private candidate without modifying it")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--library-prefix", type=Path)
    parser.add_argument("--dependency-sources", type=Path)
    args = parser.parse_args()
    if args.verify:
        if args.source_root or args.destination or args.library_prefix or args.dependency_sources:
            parser.error("--verify cannot be combined with staging inputs")
        manifest = verify_source_snapshot(args.verify)
    else:
        if not all((args.source_root, args.destination, args.library_prefix)):
            parser.error("Staging requires --source-root, --destination and --library-prefix")
        manifest = stage_repository(args.source_root, args.destination, args.library_prefix, args.dependency_sources)
    print(f"Verified {len(manifest['copied_inputs'])} private source candidate inputs.")
    print("Not published, not release-ready, and not proof of historical corresponding-source compliance.")


if __name__ == "__main__":
    main()
