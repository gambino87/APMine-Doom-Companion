"""Run finite, owned offline gameplay cases against a private diagnostic build."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .bootstrap import CORE_BINARIES, GAMES, RUNTIME_LIBRARIES, read_rules
from .dependency_sources import digest
from .local_assets import build_local_cache
from .release_source_link import cache_path
from .source_repository import validate_prepared_source


CASES = {"carry": 0, "pistol": 1, "resume": 2}
PASS = re.compile(r"^APMINE_GAMEPLAY: PASS mode=([012]) game=(doom2?) pickup=(\d+) key=(\d+) completion_recorded=1$", re.M)


def run_case(executable: Path, libraries: Path, iwads: dict[str, Path], rules: dict[str, tuple[Path, bytes]],
             assets: Path, output: Path, game: str, case: str) -> dict:
    if game not in GAMES or case not in CASES:
        raise ValueError("Unknown owned offline gameplay case")
    directory = output / f"{game}-{case}"
    directory.mkdir()
    (directory / "games").mkdir()
    (directory / "APMine-private-gameplay.marker").write_bytes(b"APMine-private-gameplay-v1\n")
    shutil.copyfile(assets, directory / "BaseAssets.zip")
    for module, (_, raw) in rules.items():
        (directory / "games" / f"{module}.apworld").write_bytes(raw)
    environment = os.environ.copy()
    environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SDL_RENDER_DRIVER="software",
                       DOOMWADDIR=str(iwads[game].parent), PATH=str(libraries / "bin") + os.pathsep + environment.get("PATH", ""))
    arguments = [str(executable), "-game", game, "-iwad", str(iwads[game]), "-practice", "-nomonsters",
                 "-nosound", "-apmine-gameplay-test", case]
    with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(arguments, cwd=directory, env=environment, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            code = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError(f"Owned offline gameplay case timed out: {game}/{case}; private logs retained")
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
    log = (directory / "stdout.log").read_text(errors="replace")
    matches = list(PASS.finditer(log))
    captures = list(directory.glob("APGAME*.png"))
    if (code or len(matches) != 1 or int(matches[0][1]) != CASES[case] or matches[0][2] != game
            or len(captures) != 1 or "native return policy passed" not in log):
        raise RuntimeError(f"Owned offline gameplay case failed: {game}/{case}; exit {code}; private logs at {directory}")
    return {"game": game, "case": case, "exit_code": code, "pickup_index": int(matches[0][3]),
            "key_index": int(matches[0][4]), "native_return_and_completion_passed": True,
            "logs": {name: {"path": str(directory / name), "sha256": digest(directory / name)}
                     for name in ("stdout.log", "stderr.log")},
            "capture": {"path": str(captures[0]), "sha256": digest(captures[0]), "visually_inspected": False}}


def collect(source: Path, build: Path, prefix: Path, iwads: dict[str, Path], rule_paths: list[Path], output: Path) -> dict:
    source, build, prefix, output = (path.resolve() for path in (source, build, prefix, output))
    if output.exists():
        raise FileExistsError("Use a new private gameplay acceptance directory")
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in
           (source, build, prefix, Path(__file__).resolve().parents[2])):
        raise ValueError("Keep private gameplay artifacts separate from source/build/library/platform inputs")
    validate_prepared_source(source)
    header = source / "src/doom/apmine_gameplay_test.h"
    canonical = Path(__file__).with_name("apmine_gameplay_test.h")
    if digest(header) != digest(canonical):
        raise ValueError("Native gameplay harness differs from its canonical source")
    practice = source / "src/archipelago/practice_items.h"
    canonical_practice = Path(__file__).with_name("practice_items.h")
    if digest(practice) != digest(canonical_practice):
        raise ValueError("Native practice lookup differs from its tested source")
    cache = build / "CMakeCache.txt"
    if (cache_path(cache, "CMAKE_HOME_DIRECTORY") != source or cache_path(cache, "CMAKE_PREFIX_PATH") != prefix
            or not re.search(r"^APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=(1|ON|TRUE)$", cache.read_text(), re.M)):
        raise ValueError("Use an explicitly diagnostic build of this prepared source and dependency prefix")
    binaries = {}
    for root, names in ((build / "bin", CORE_BINARIES), (prefix / "bin", RUNTIME_LIBRARIES)):
        lookup = {path.name.casefold(): path for path in root.iterdir() if path.is_file()}
        for name in names:
            path = lookup.get(name)
            if path is None or path.is_symlink():
                raise ValueError(f"Missing regular private gameplay binary: {name}")
            binaries[path] = digest(path)
    if set(iwads) != set(GAMES):
        raise ValueError("Supply explicit original DOS Doom and Doom II IWADs")
    iwads = {game: path.resolve() for game, path in iwads.items()}
    consumed = {**binaries, header: digest(header), canonical: digest(canonical), cache: digest(cache),
                practice: digest(practice), canonical_practice: digest(canonical_practice)}
    consumed.update({source / name: digest(source / name) for name in
                    ("src/doom/d_main.c", "src/doom/CMakeLists.txt", "src/doom/g_game.c", "src/doom/p_inter.c", "src/archipelago/apdoom.cpp")})
    rules = {}
    for path in rule_paths:
        game, raw = read_rules(path)
        module = GAMES[game][0]
        if module in rules:
            raise ValueError("Duplicate private gameplay rule package")
        rules[module] = (path, raw)
        consumed[path] = digest(path)
    if set(rules) != {record[0] for record in GAMES.values()}:
        raise ValueError("Supply both reviewed rules-only Doom APWorlds")
    consumed.update({path: digest(path) for path in iwads.values()})
    output.mkdir(parents=True)
    results = []
    try:
        assets = build_local_cache(list(iwads.values()), output / "asset-cache")
        for game in GAMES:
            for case in CASES:
                results.append(run_case(build / "bin/archipelago-doom.exe", prefix, iwads, rules, assets, output, game, case))
    finally:
        if any(digest(path) != checksum for path, checksum in consumed.items()):
            raise ValueError("Private gameplay source/native/resource inputs changed during acceptance")
    return {"schema_version": 1, "release_ready": False, "published": False,
            "status": "private-offline-native-gameplay-acceptance", "gameplay_diagnostics": True, "runs": results,
            "build": {"path": str(build), "configuration_sha256": consumed[cache]},
            "native_inputs": [{"name": path.name, "sha256": checksum} for path, checksum in sorted(binaries.items())],
            "source_inputs": [{"path": str(path), "sha256": consumed[path]} for path in consumed if path.is_relative_to(source)],
            "private_asset_cache_sha256": digest(assets),
            "scope": "Actual spawned pickups/AP practice keys, native level-select save/resume/pistol reset and native completion state",
            "limitations": ["Owned offline practice only; no live AP server or LocationChecks transmission was tested",
                            "Diagnostic fixture changes actor state; normal builds keep the harness disabled",
                            "Audio is disabled and rendered captures need separate visual inspection",
                            "Not public source delivery, historical source matching or art/music permission",
                            "No original art/native payload is included in this metadata report"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "build", "library-prefix", "doom-iwad", "doom2-iwad", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--apworld", type=Path, action="append", required=True)
    args = parser.parse_args()
    result = collect(args.source, args.build, args.library_prefix, {"doom": args.doom_iwad, "doom2": args.doom2_iwad}, args.apworld, args.output)
    with (args.output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(f"Passed {len(result['runs'])} owned offline native gameplay cases. Private evidence: {args.output}")
    print("No room was contacted; no package, source repository or public release was created.")


if __name__ == "__main__":
    main()
