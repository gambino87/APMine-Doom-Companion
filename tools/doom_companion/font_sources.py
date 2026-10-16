"""Retain only the exact reviewed upstream font PNGs with their original notices."""
# APMine companion change, 2026-10-15: record exact upstream font inputs and license context.

from __future__ import annotations

from pathlib import Path

from .inventory import sha256


UPSTREAM_REVISION = "e6f632a4397023b6b17cc312525a16b2aacd64d6"
FONT_FILES = {
    "textscreen/fonts/small.png": "0cc2592a2a93cf1d4ae156de9be8b60ea90917be7734d3eb3459f0dffcc912f4",
    "textscreen/fonts/normal.png": "76fbe6bfea0dc873d15b9dddb66b0fb2f94b89445667acce33786b830cdd39c6",
    "textscreen/fonts/large.png": "f506b90be8fe20566e85ae2cb787a298d999dd0c8a17b515924061f57a7d9aaf",
}
CONTEXT_FILES = {
    "textscreen/fonts/README": "81db9935f1592f3856af85cb000ac4b25e6066ecc651d5558487d8adc8039dcb",
    "textscreen/fonts/convert-font": "3a44c580be759c5f533ed981314c110fe44816a89f1ab8b4415d45d5bca5e7de",
    "COPYING.md": "aef8b4222b79d0dbf6bf17cfff71c90a6a6bb8917a4162abe417b469ed22da2e",
}
MAX_INPUT_BYTES = 128 * 1024


def read_input(root: Path, name: str) -> bytes:
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Reviewed font source inputs cannot follow links")
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"Missing or oversized reviewed font source input: {name}")
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Reviewed font source input grew beyond its bound")
    return raw


def collect(root: Path) -> tuple[dict[str, bytes], dict | None]:
    # Legacy snapshots can be checked without claiming their omitted PNG inputs
    # are present. Any PNG inclusion requires the full exact reviewed set.
    if not any((root / name).exists() or (root / name).is_symlink() for name in FONT_FILES):
        return {}, None
    payloads, records = {}, []
    for name, expected in {**FONT_FILES, **CONTEXT_FILES}.items():
        raw = read_input(root, name)
        if sha256(raw) != expected:
            raise ValueError(f"Font source/notice differs from the immutable upstream review: {name}")
        if name in FONT_FILES:
            payloads[name] = raw
        records.append({"path": name, "bytes": len(raw), "sha256": expected,
                        "source_url": f"https://raw.githubusercontent.com/chocolate-doom/chocolate-doom/{UPSTREAM_REVISION}/{name}"})
    profile = {"schema_version": 1, "status": "reviewed-upstream-font-source-inputs",
        "upstream_revision": UPSTREAM_REVISION, "inputs": sorted(records, key=lambda entry: entry["path"]),
        "normal_font_origin": {"project": "DOSBox", "release": "0.74-3",
            "archive_sha256": "c0d13dd7ed2ed363b68de615475781e891cd582e8162b5c3669137502222260a",
            "source_path": "src/ints/int10_memory.cpp",
            "source_sha256": "69f641681c3c2ac7ff8269360554dfaf15b807a7825def8885475158a6944c93",
            "glyph_table_sha256": "c7dd5990fa5401649cf69e9bbb4c8a51f86865939b9318281a27de7f314eda3a",
            "all_256_glyphs_match": True, "source_header_license": "GPL-2.0-or-later"},
        "notice_context": "Original DOSBox, Thomas A. Fine permission and Simon Howard notices remain in the font README; the full upstream GPL remains in COPYING.md",
        "source_delivery_complete": False, "historical_corresponding_source_complete": False,
        "scope": "Three exact upstream font PNG inputs only; no game artwork or aggregate historical rights clearance"}
    return payloads, profile
