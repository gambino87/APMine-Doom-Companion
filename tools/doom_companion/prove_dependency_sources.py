"""Compare staged preferred sources to checksum-pinned archives plus patches."""

import argparse
import difflib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

from .dependency_sources import digest, regular_files, unused_upstream_prebuilt, verify


def prove(bundle: Path, downloads: Path) -> dict:
    bundle = bundle.resolve()
    manifest = verify(bundle)
    omitted = {entry["path"] for entry in manifest["omitted_inputs"]}
    report = {"schema_version": 1, "release_ready": False, "published": False, "dependencies": []}
    for dependency in manifest["dependencies"]:
        name = dependency["name"]
        if not dependency["preferred_source"].startswith("sources/"):
            continue
        resources = [resource for resource in dependency["resources"] if "verified_archive" in resource]
        if len(resources) != 1:
            raise ValueError(f"Expected one primary source archive: {name}")
        resource = resources[0]
        archive = downloads / resource["verified_archive"]
        if digest(archive, "sha512") != resource["sha512"]:
            raise ValueError(f"Upstream source archive checksum differs: {name}")
        with tempfile.TemporaryDirectory(prefix="apmine-dependency-source-proof-") as directory:
            temporary = Path(directory).resolve()
            if not temporary.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise ValueError("Unexpected temporary extraction directory")
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                if len(members) > 50000 or sum(member.size for member in members) > 1024**3:
                    raise ValueError("Source archive exceeds extraction bounds")
                regular_members = []
                if len({member.name for member in members}) != len(members):
                    raise ValueError("Duplicate source archive members")
                missing_aliases = []
                android_aliases = {
                    "android-project-ant/AndroidManifest.xml": "../android-project/app/src/main/AndroidManifest.xml",
                    "android-project-ant/src": "../android-project/app/src/main/java",
                }
                for member in members:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or ":" in member.name or "\\" in member.name:
                        raise ValueError(f"Unsafe source archive member: {member.name}")
                    relative = "/".join(path.parts[1:])
                    if unused_upstream_prebuilt(name, relative):
                        continue
                    if member.isdir() or member.isfile():
                        regular_members.append(member)
                        continue
                    if name == "sdl2" and member.issym() and android_aliases.get(relative) == member.linkname:
                        # The recorded Windows extraction omits these two
                        # legacy Android aliases; their targets remain present.
                        missing_aliases.append({"path": relative, "target": member.linkname})
                    else:
                        raise ValueError(f"Non-regular source archive member: {member.name}")
                tar.extractall(temporary, members=regular_members, filter="data")
            roots = list(temporary.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise ValueError(f"Unexpected upstream archive layout: {name}")
            original = roots[0]
            for patch in dependency["applied_patches"]:
                subprocess.run(["git", "apply", "--whitespace=nowarn", str(bundle / "ports" / name / patch)],
                               cwd=original, check=True, capture_output=True, text=True, timeout=30)
            expected = {path.relative_to(original).as_posix(): digest(path) for path in regular_files(original)
                        if f"sources/{name}/{path.relative_to(original).as_posix()}" not in omitted}
            provided_root = bundle / "sources" / name
            provided = {path.relative_to(provided_root).as_posix(): digest(path)
                        for path in regular_files(provided_root)}
            line_endings = []
            for key in provided.keys() & expected.keys():
                if provided[key] == expected[key] or Path(key).suffix not in (".txt", ".cmake", ".c", ".h", ".cpp", ".hpp", ".in", ".def"):
                    continue
                old = (original / key).read_bytes()
                new = (provided_root / key).read_bytes()
                if b"\0" not in old + new and old.replace(b"\r\n", b"\n") == new.replace(b"\r\n", b"\n"):
                    line_endings.append({"path": key, "upstream_sha256": expected[key], "provided_sha256": provided[key]})
                    expected[key] = provided[key]
            if provided != expected:
                changed = sorted(key for key in provided.keys() & expected.keys() if provided[key] != expected[key])
                for key in changed[:3]:
                    old = (original / key).read_text(errors="replace")
                    new = (provided_root / key).read_text(errors="replace")
                    print("".join(list(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                          fromfile=f"upstream/{key}", tofile=f"provided/{key}"))[:30]))
                raise ValueError(f"Preferred source differs from archive/patches: {name}; "
                                 f"missing={sorted(expected.keys() - provided.keys())}; "
                                 f"extra={sorted(provided.keys() - expected.keys())}; changed={changed}")
            report["dependencies"].append({"name": name, "upstream_sha512": resource["sha512"],
                                           "patches": dependency["applied_patches"],
                                           "verified_files": len(provided), "source_match": True,
                                           "line_ending_only_differences": line_endings,
                                           "omitted_legacy_android_aliases": missing_aliases})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Refusing to overwrite an existing source proof report")
    report = prove(args.bundle, args.downloads)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"Verified {len(report['dependencies'])} preferred library sources against upstream archives and patches.")


if __name__ == "__main__":
    main()
