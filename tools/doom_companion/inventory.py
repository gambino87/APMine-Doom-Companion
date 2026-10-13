"""Inventory a retired APDoom release without extracting or republishing assets."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
from typing import Iterator
import zipfile


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_NESTING = 4
MAX_EMBEDDED_CANDIDATES = 128
GAMEPLAY_DIAGNOSTIC_MARKER = b"APMINE_GAMEPLAY: PASS"
NETWORK_DIAGNOSTIC_MARKER = b"APMINE_NETWORK: PASS"
DIAGNOSTIC_MARKERS = (GAMEPLAY_DIAGNOSTIC_MARKER, NETWORK_DIAGNOSTIC_MARKER)
CODE_SUFFIXES = {
    ".c", ".cpp", ".h", ".hpp", ".cmake", ".py", ".in", ".am", ".ac",
    ".sh", ".bat", ".ps1", ".rc", ".s", ".asm", ".def",
}
NOTICE_NAMES = {"LICENSE", "LICENSE.txt", "COPYING", "COPYING.md", "AUTHORS", "CREDITS"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wad_lumps(data: bytes) -> Iterator[tuple[str, bytes]]:
    if len(data) < 12:
        raise ValueError("Truncated WAD header")
    magic, count, directory = struct.unpack_from("<4sii", data)
    if magic not in (b"IWAD", b"PWAD") or not 0 <= count <= 100000:
        raise ValueError("Invalid WAD header")
    if directory < 12 or directory + count * 16 > len(data):
        raise ValueError("WAD directory is outside the file")
    for index in range(count):
        offset, size, raw_name = struct.unpack_from("<ii8s", data, directory + index * 16)
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError(f"WAD lump {index} is outside the file")
        yield raw_name.rstrip(b"\0").decode("ascii"), data[offset:offset + size]


def embedded_zips(data: bytes) -> Iterator[bytes]:
    """CMake's embedded archives are contiguous ZIP blobs inside native files."""
    position = 0
    for _ in range(MAX_EMBEDDED_CANDIDATES):
        start = data.find(b"PK\x03\x04", position)
        if start < 0:
            return
        position = start + 4
        end = data.find(b"PK\x05\x06", start)
        while end >= 0 and end - start <= MAX_ARCHIVE_BYTES:
            if end + 22 > len(data):
                break
            comment_length = struct.unpack_from("<H", data, end + 20)[0]
            stop = end + 22 + comment_length
            if stop <= len(data):
                candidate = data[start:stop]
                try:
                    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
                        # Exclude false signatures and a later ZIP's end record.
                        if archive.start_dir >= 0 and all(info.header_offset >= 0 for info in archive.infolist()):
                            yield candidate
                            position = stop
                            break
                except (ValueError, zipfile.BadZipFile):
                    pass
            end = data.find(b"PK\x05\x06", end + 4)
    if data.find(b"PK\x03\x04", position) >= 0:
        raise ValueError("Too many embedded ZIP candidates; inspect the native file separately")


def inspect_release(data: bytes, reference_wads: list[Path], build_directory: Path) -> dict:
    reference_index: dict[tuple[int, str], list[str]] = {}
    for path in reference_wads:
        for name, raw in wad_lumps(path.read_bytes()):
            if raw:
                reference_index.setdefault((len(raw), sha256(raw)), []).append(f"{path.name}:{name}")
    result = {
        "sha256": sha256(data), "native_binaries": [], "original_asset_matches": [],
        "archives": [], "embedded_archives": [], "scan_warnings": [],
    }
    remaining_bytes = MAX_ARCHIVE_BYTES

    def inspect(raw: bytes, label: str, depth: int, embedded: bool = False) -> None:
        nonlocal remaining_bytes
        if depth > MAX_NESTING:
            raise ValueError("Archive nesting exceeds the recovery scanner's limit")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            required_bytes = sum(info.file_size for info in infos)
            if required_bytes > remaining_bytes:
                raise ValueError("Archive expansion exceeds the recovery scanner's limit")
            remaining_bytes -= required_bytes
            result["archives"].append({
                "path": label, "sha256": sha256(raw), "members": len(infos),
                "notice_members": [info.filename for info in infos if any(
                    word in info.filename.lower() for word in ("license", "copying", "notice", "readme")
                )],
            })
            for info in infos:
                if info.is_dir():
                    continue
                member = info.filename.replace("\\", "/")
                member_label = f"{label}!{member}"
                payload = archive.read(info)
                suffix = PurePosixPath(member).suffix.lower()
                if suffix in (".exe", ".dll"):
                    candidate = build_directory / PurePosixPath(member).name
                    result["native_binaries"].append({
                        "path": member_label, "sha256": sha256(payload),
                        "matches_local_build": candidate.is_file() and sha256(candidate.read_bytes()) == sha256(payload),
                    })
                    for index, blob in enumerate(embedded_zips(payload)):
                        child = f"{member_label}!embedded-zip-{index}"
                        result["embedded_archives"].append(child)
                        inspect(blob, child, depth + 1, embedded=True)
                elif suffix in (".zip", ".apworld", ".pk3"):
                    inspect(payload, member_label, depth + 1, embedded=embedded)
                elif suffix == ".wad":
                    for name, lump in wad_lumps(payload):
                        references = reference_index.get((len(lump), sha256(lump)))
                        if references:
                            result["original_asset_matches"].append({
                                "path": member_label, "lump": name, "bytes": len(lump),
                                "reference_lumps": references, "embedded_in_native_binary": embedded,
                            })

    inspect(data, "release", 0)
    result["scan_warnings"] = [
        "Exact-byte matches are evidence of copied assets, not an exhaustive derivative-art audit.",
        "Only the supplied reference WADs were compared; renamed/edited assets and other games need review.",
        "Binary equality with a build output does not prove the current source tree produced that output.",
    ]
    return result


def source_inventory(source_root: Path) -> dict:
    code = []
    notices = []
    omitted = []
    stack = [source_root]
    while stack:
        directory = stack.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(source_root).as_posix()
            if path.is_symlink():
                omitted.append({"path": relative, "reason": "symlink; not followed"})
            elif path.is_dir():
                if path.name == ".git" or path.name == "__pycache__" or path.name.startswith(("build", ".venv")):
                    continue
                stack.append(path)
            elif path.name in {"doom_icon.c", "heretic_icon.c", "hexen_icon.c", "strife_icon.c", "setup_icon.c"}:
                omitted.append({"path": relative, "reason": "generated icon pixel array; requires asset review"})
            elif path.name in NOTICE_NAMES:
                notices.append({"path": relative, "sha256": sha256(path.read_bytes())})
            elif path.suffix.lower() in CODE_SUFFIXES or path.name in ("CMakeLists.txt", "vcpkg.json", ".gitmodules"):
                code.append({"path": relative, "sha256": sha256(path.read_bytes())})
            elif path.suffix.lower() in (".zip", ".wad", ".png", ".ico", ".dll", ".exe", ".pk3", ".apworld", ".gz", ".xz", ".tar"):
                omitted.append({"path": relative, "reason": "asset/archive/binary; needs separate provenance review"})
    components = []
    for relative in (".", "APCpp", "APCpp/IXWebSocket", "APCpp/jsoncpp"):
        directory = source_root / relative
        component = {"path": relative, "revision": None, "working_tree_status": None}
        if (directory / ".git").exists():
            for command, field in ((["rev-parse", "HEAD"], "revision"), (["status", "--porcelain"], "working_tree_status")):
                process = subprocess.run(["git", "-c", f"safe.directory={directory.resolve()}",
                                          "-C", str(directory), *command], capture_output=True, text=True, check=True)
                component[field] = process.stdout.strip()
        components.append(component)
    return {"code_inputs": sorted(code, key=lambda record: record["path"]),
            "notice_inputs": sorted(notices, key=lambda record: record["path"]),
            "unreviewed_non_source_inputs": sorted(omitted, key=lambda record: record["path"]),
            "components": components}


def snapshot_recovery_source(source_root: Path, source: dict, output: Path) -> dict:
    """Preserve candidate code/notice bytes privately; never claim completeness."""
    repository = Path(__file__).resolve().parents[2]
    forbidden = ("apps/web/hosted-files", "build/apworlds", "WebHostLib/static")
    if any(output.resolve().is_relative_to(repository / directory) for directory in forbidden):
        raise ValueError("Incomplete recovery snapshots cannot be written to a download/public-asset location")
    if output.suffix.lower() != ".zip":
        raise ValueError("Recovery source snapshots must use a .zip path")
    inputs = sorted(source["code_inputs"] + source["notice_inputs"], key=lambda entry: entry["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "release_ready": False, "status": "incomplete-private-recovery-snapshot",
                "inputs": inputs, "components": source["components"],
                "unreviewed_non_source_inputs": source["unreviewed_non_source_inputs"]}
    with tempfile.TemporaryFile(dir=output.parent) as temporary:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in inputs:
                path = source_root / entry["path"]
                if not path.resolve().is_relative_to(source_root.resolve()):
                    raise ValueError(f"Source input now resolves outside the inspected tree: {entry['path']}")
                raw = path.read_bytes()
                if sha256(raw) != entry["sha256"]:
                    raise ValueError(f"Source changed since inventory; repeat the scan: {entry['path']}")
                archive.writestr(f"source/{entry['path']}", raw)
            archive.writestr("recovery-manifest.json", json.dumps(manifest, indent=2) + "\n")
            archive.writestr("NOT_FOR_DISTRIBUTION.txt", (
                "PRIVATE, INCOMPLETE RECOVERY SNAPSHOT\n"
                "This is not a corresponding-source release or legal clearance.\n"
                "Asset/build inputs and historical provenance remain unresolved.\n"
                "Do not distribute it or restore the retired client download.\n"
            ))
        temporary.seek(0)
        # All source hashes are checked before reserving a destination. An
        # existing recovery file is never replaced, including on a concurrent run.
        with output.open("xb") as destination:
            try:
                shutil.copyfileobj(temporary, destination)
            except BaseException:
                destination.close()
                output.unlink()
                raise
    return {"path": str(output), "sha256": sha256(output.read_bytes()),
            "inputs": len(inputs), "release_ready": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    release = parser.add_mutually_exclusive_group(required=True)
    release.add_argument("--release-zip", type=Path)
    release.add_argument("--legacy-ref", help="Git object such as commit:path/to/retired-release.zip; read-only")
    parser.add_argument("--iwad", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, help="Optional local inventory JSON; contains hashes, not asset bytes")
    parser.add_argument("--source-snapshot", type=Path, help="Optional private, incomplete code/notice snapshot ZIP")
    args = parser.parse_args()
    data = args.release_zip.read_bytes() if args.release_zip else subprocess.check_output(["git", "show", args.legacy_ref])
    report = {
        "schema_version": 1, "release_ready": False,
        "status": "recovery-inventory-only",
        "unresolved": [
            "Verify source/build inputs actually correspond to every previously distributed GPL binary.",
            "Review embedded and external assets; do not republish the retired payload.",
            "Deliver corresponding source/notices to recipients; this local inventory is not source delivery.",
        ],
        "source": source_inventory(args.source_root),
        "release": inspect_release(data, args.iwad, args.build_dir),
    }
    if args.source_snapshot:
        report["source_snapshot"] = snapshot_recovery_source(args.source_root, report["source"], args.source_snapshot)
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(output)
        print(f"Local recovery inventory saved to {args.output}; not a cleared release.")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
