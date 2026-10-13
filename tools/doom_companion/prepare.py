"""Prepare a private source working copy, excluding original-game resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .inventory import CODE_SUFFIXES, NOTICE_NAMES, sha256


TEXT_SUFFIXES = CODE_SUFFIXES | {".cin", ".xml", ".json", ".txt", ".md", ".rst", ".pc", ".inl", ".inc", ".cc", ".cxx", ".hh", ".hxx"}
SKIP_DIRECTORIES = {".git", "__pycache__", "data", "quickcheck"}
GENERATED_NAMES = {"embedded_files.c", "embedded_files.h", "doom_icon.c", "heretic_icon.c", "hexen_icon.c", "strife_icon.c", "setup_icon.c"}


def prepare_source(source_root: Path, destination: Path) -> dict:
    source_root = source_root.resolve()
    destination = destination.resolve()
    repository = Path(__file__).resolve().parents[2]
    if destination.is_relative_to(source_root) or source_root.is_relative_to(destination):
        raise ValueError("Use a separate, non-overlapping companion source directory")
    if destination.is_relative_to(repository):
        raise ValueError("The engine source working copy must stay outside the platform repository")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace an existing source working copy: {destination}")
    if not (source_root / "COPYING.md").is_file() or not (source_root / "APCpp/LICENSE").is_file():
        raise ValueError("APDoom and APCpp license inputs are required")
    inputs = []
    skipped = []
    stack = [source_root]
    while stack:
        directory = stack.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(source_root).as_posix()
            if path.is_symlink():
                skipped.append(relative)
            elif path.is_dir():
                if path.name in SKIP_DIRECTORIES or path.name.startswith(("build", ".venv")):
                    continue
                stack.append(path)
            elif path.name in GENERATED_NAMES:
                skipped.append(relative)
            elif path.name in NOTICE_NAMES or path.name == "CMakeLists.txt" or path.suffix.lower() in TEXT_SUFFIXES:
                inputs.append((relative, path))
            # This is a source dependency, not a game asset pack. It remains a
            # separately reviewable build input in the private working copy.
            elif relative == "APCpp/mbedtls-3.4.0.tar.gz":
                inputs.append((relative, path))
            else:
                skipped.append(relative)
    destination.mkdir(parents=True)
    copied = []
    for relative, source in inputs:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"path": relative, "sha256": sha256(target.read_bytes())})
    manifest = {"schema_version": 1, "release_ready": False, "status": "private-source-working-copy",
                "source_root": str(source_root), "copied_inputs": sorted(copied, key=lambda item: item["path"]),
                "skipped_resources": sorted(skipped)}
    (destination / "prepare-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare_source(args.source_root, args.destination)
    print(f"Prepared {len(manifest['copied_inputs'])} source inputs at {args.destination}.")
    print("Private working copy only; review/remove embedding, icons and publication hooks before building.")


if __name__ == "__main__":
    main()
