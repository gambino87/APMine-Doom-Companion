"""Recover a recipient's exact historical build input locally; GPL-3.0-or-later.

APMine helper, 2026-10-16. Reads a supplied native file without executing it.
The recovered resource is not licensed or offered for redistribution by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct


ASSET_BYTES = 268642
ASSET_SHA256 = "08d10f7e4819497e176814b1e0a4f1de46a3073b5b6ea9da6234ecf96405a170"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_CANDIDATES = 128


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_path(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
           for part in (path, *path.parents)):
        raise ValueError("Linked input/output paths are not accepted")
    return path.resolve()


def read_binary(path: Path) -> bytes:
    if not path.is_file() or not 64 <= path.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("Expected a bounded regular native input file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if not 64 <= len(raw) <= MAX_INPUT_BYTES:
        raise ValueError("Native input size changed or exceeds its bound")
    pe_offset = struct.unpack_from("<I", raw, 60)[0]
    if raw[:2] != b"MZ" or pe_offset < 64 or raw[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("Input does not have the expected PE file signatures")
    return raw


def recover(binary: Path, output: Path) -> dict:
    binary, output = map(checked_path, (binary, output))
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Output directory must be new, with an existing parent")
    raw = read_binary(binary)
    binary_hash = sha256(raw)
    offsets = []
    position = 0
    count = 0
    while (start := raw.find(b"PK\x03\x04", position)) >= 0:
        count += 1
        if count > MAX_CANDIDATES:
            raise ValueError("Too many embedded ZIP candidates")
        position = start + 4
        candidate = raw[start:start + ASSET_BYTES]
        if len(candidate) == ASSET_BYTES and sha256(candidate) == ASSET_SHA256:
            offsets.append(start)
    if len(offsets) != 1:
        raise ValueError("Expected exactly one byte-exact historical BaseAssets.zip")
    if sha256(read_binary(binary)) != binary_hash:
        raise ValueError("Native input changed during recovery")
    start = offsets[0]
    asset = raw[start:start + ASSET_BYTES]
    # All validation precedes output creation. Neither an existing directory nor
    # either output file is overwritten, and no archive members are unpacked.
    output.mkdir()
    destination = output / "BaseAssets.zip"
    with destination.open("xb") as stream:
        stream.write(asset)
    if destination.read_bytes() != asset:
        raise ValueError("Recovered archive did not preserve the exact input bytes")
    result = {
        "status": "exact-historical-player-resource-recovered-locally",
        "input": str(binary), "inputBytes": len(raw), "inputSha256": binary_hash,
        "output": str(destination), "embeddedOffset": start,
        "archiveBytes": ASSET_BYTES, "archiveSha256": ASSET_SHA256,
        "nativeInputExecuted": False, "archiveMembersUnpacked": False,
        "resourceRedistributionPermissionEstablished": False,
        "preferredResourceSourceEstablished": False,
        "historicalSourceDeliveryOrCureEstablished": False,
        "published": False,
    }
    with (output / "RECOVERY.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True,
                        help="Your existing historical companion EXE or DLL; it will only be read")
    parser.add_argument("--output-directory", type=Path, required=True,
                        help="New private local directory, with an existing parent")
    args = parser.parse_args()
    print(json.dumps(recover(args.binary, args.output_directory), indent=2))


if __name__ == "__main__":
    main()
