"""Exercise native gameplay against an owned, ephemeral loopback AP server."""

from __future__ import annotations

import argparse
import asyncio
import functools
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from .bootstrap import CORE_BINARIES, GAMES, RUNTIME_LIBRARIES, read_rules
from .dependency_sources import digest
from .local_assets import build_local_cache
from .release_source_link import cache_path
from .source_repository import validate_prepared_source


EXPECTED = {"max_health": 203, "max_armor": 205, "max_ammo": [220, 50, 300, 50],
            "enhanced_backpack_count": 1, "weapon_owned": [1, 1, 1, 1, 0, 0, 0, 1, 0]}
INITIAL_ITEMS = ["Small Health Upgrade"] * 2 + ["Armor Upgrade"] + ["Bullet Capacity Upgrade"] * 3 + [
    "Enhanced Backpack", "Progressive weapon", "Progressive weapon"]
KEY_TYPES = {5, 6, 13, 40, 39, 38}


def fixture_catalog(definitions: dict) -> tuple[dict[str, int], dict[int, int]]:
    items = {entry[0]: int(identifier) for identifier, entry in definitions["item_table"].items()}
    map_items = {entry[1]: int(identifier) for identifier, entry in definitions["item_table"].items()
                 if entry[2:] == [1, 2]}
    if -1 not in map_items:
        raise ValueError("Owned network fixture needs the second map's access item")
    placements = {}
    things = definitions["level_info"][0][1]["thing_list"]
    for index, identifier in definitions["location_table"]["1"]["2"].items():
        doom_type = things[int(index)][0] if int(index) >= 0 else None
        if doom_type in KEY_TYPES:
            if doom_type not in map_items:
                raise ValueError("Owned fixture key has no matching per-map AP item")
            placements[int(identifier)] = map_items[doom_type]
        else:
            placements[int(identifier)] = items["Medikit"]
    items["fixture_map_access"] = map_items[-1]
    return items, placements


def fixture_placements(definitions: dict, available: dict[int, tuple]) -> tuple[dict[str, int], dict[int, int]]:
    items, placements = fixture_catalog(definitions)
    placements = {location: item for location, item in placements.items() if location in available}
    completion = int(definitions["location_table"]["1"]["2"]["-1"])
    keys = {int(identifier) for identifier, entry in definitions["item_table"].items()
            if entry[1] in KEY_TYPES and entry[2:] == [1, 2]}
    if completion not in placements or len(placements) < 3 or not keys & set(placements.values()):
        raise ValueError("Generated test room lacks pickup/key/completion fixture checks")
    return items, placements


def require_private_output(output: Path, roots: tuple[Path, ...]) -> None:
    if output.exists():
        raise FileExistsError("Use a fresh owned network gameplay directory")
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):
        raise ValueError("Private network outputs must stay separate from source/build/platform inputs")
    if os.name != "nt" or not output.is_relative_to((Path(os.environ["LOCALAPPDATA"]) / "APMine").resolve()):
        raise ValueError("This native acceptance runner requires private Windows LOCALAPPDATA/APMine output")


async def run_native(executable: Path, prefix: Path, iwad: Path, directory: Path,
                     game: str, player: str, port: int, phase: str, context, slot: int,
                     definitions: dict) -> dict:
    from MultiServer import get_received_items

    if phase not in {"initial", "reconnect"} or game not in GAMES:
        raise ValueError("Unknown owned network case")
    environment = os.environ.copy()
    environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SDL_RENDER_DRIVER="software",
                       DOOMWADDIR=str(iwad.parent), PATH=str(prefix / "bin") + os.pathsep + environment.get("PATH", ""))
    args = [str(executable), "-game", game, "-iwad", str(iwad), "-apserver", f"127.0.0.1:{port}",
            "-applayer", player, "-apsavedir", str(directory / "saves"),
            "-nomonsters", "-nosound", "-apmine-network-test", phase]
    stdout_path, stderr_path = (directory / f"{phase}-{name}.log" for name in ("stdout", "stderr"))
    expected_checks = {int(definitions["location_table"]["1"]["2"]["-1"])}
    grants_sent = False
    confirmed = False
    deadline = asyncio.get_running_loop().time() + 65
    received_before = len(get_received_items(context, 0, slot, True))
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = await asyncio.create_subprocess_exec(*args, cwd=directory, env=environment, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while process.returncode is None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(f"Owned native network case timed out: {game}/{phase}")
                log = stdout_path.read_text(errors="replace")
                for label in ("pickup", "key"):
                    match = re.search(rf"^APMINE_NETWORK: {label}=(\d+)$", log, re.M)
                    if match:
                        expected_checks.add(int(definitions["location_table"]["1"]["2"][match[1]]))
                checks = context.location_checks[0, slot]
                if phase == "initial" and len(expected_checks) == 3 and len(expected_checks & checks) >= 2 and not grants_sent:
                    for item in ("Small Health Upgrade", "Bullet Capacity Upgrade", "Progressive weapon"):
                        if not context.commandprocessor._cmd_send(player, item):
                            raise RuntimeError(f"Owned fixture failed to send {item}")
                    grants_sent = True
                if phase == "initial" and grants_sent and expected_checks <= checks and not confirmed:
                    (directory / "APMine-server-checks-confirmed.marker").write_bytes(b"APMine-network-checks-v1\n")
                    confirmed = True
                await asyncio.sleep(0.05)
            code = await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    log = stdout_path.read_text(errors="replace")
    marker = f"APMINE_NETWORK: PASS {phase} health=203 bullets=220"
    captures = sorted(directory.glob("APNET*.png"))
    states = list((directory / "saves").rglob("apstate.json"))
    if code or marker not in log or "APDOOM: Authenticated" not in log or len(states) != 1:
        raise RuntimeError(f"Owned native network case failed: {game}/{phase}, exit {code}; private logs at {directory}")
    state = json.loads(states[0].read_text())
    if any(state["player"].get(key) != value for key, value in EXPECTED.items()):
        raise RuntimeError("Native persisted AP inventory did not match the independently expected fixture values")
    if phase == "initial" and (not confirmed or not expected_checks <= context.location_checks[0, slot]):
        raise RuntimeError("The real AP server did not confirm pickup, key and completion checks")
    if phase == "reconnect" and len(get_received_items(context, 0, slot, True)) != received_before:
        raise RuntimeError("Reconnect unexpectedly checked/rewarded another location")
    if len(captures) != (1 if phase == "initial" else 2):
        raise RuntimeError("Native network fixture did not capture the actual map for each connection")
    return {"game": game, "phase": phase, "exit_code": code, "player": player,
            "server_confirmed_checks": sorted(context.location_checks[0, slot]),
            "expected_pickup_key_completion_checks": sorted(expected_checks) if phase == "initial" else None,
            "persisted_player": EXPECTED, "state_sha256": digest(states[0]),
            "capture": {"path": str(captures[-1]), "sha256": digest(captures[-1]), "visually_inspected": False},
            "logs": {path.name: digest(path) for path in (stdout_path, stderr_path)}}


async def collect(source: Path, build: Path, prefix: Path, room: Path,
                  iwads: dict[str, Path], rule_paths: list[Path], output: Path) -> dict:
    source, build, prefix, room, output = (path.resolve() for path in (source, build, prefix, room, output))
    require_private_output(output, (source, build, prefix, room.parent, Path(__file__).resolve().parents[2]))
    validate_prepared_source(source)
    consumed = {room: digest(room)}
    for name in ("apmine_gameplay_test.h", "apmine_network_test.h", "practice_items.h"):
        native = source / ("src/archipelago" if name == "practice_items.h" else "src/doom") / name
        canonical = Path(__file__).with_name(name)
        if digest(native) != digest(canonical):
            raise ValueError(f"Native {name} differs from its tested canonical source")
        consumed.update({native: digest(native), canonical: digest(canonical)})
    cache = build / "CMakeCache.txt"
    if (cache_path(cache, "CMAKE_HOME_DIRECTORY") != source or cache_path(cache, "CMAKE_PREFIX_PATH") != prefix
            or not re.search(r"^APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=(1|ON|TRUE)$", cache.read_text(), re.M)):
        raise ValueError("Only an explicitly diagnostic build of this private source can run network automation")
    consumed[cache] = digest(cache)
    for name in ("src/doom/d_main.c", "src/doom/CMakeLists.txt", "src/doom/g_game.c", "src/doom/p_inter.c", "src/archipelago/apdoom.cpp"):
        consumed[source / name] = digest(source / name)
    for root, names in ((build / "bin", CORE_BINARIES), (prefix / "bin", RUNTIME_LIBRARIES)):
        for name in names:
            consumed[root / name] = digest(root / name)
    if set(iwads) != set(GAMES):
        raise ValueError("Supply explicit original Doom and Doom II IWADs")
    iwads = {game: path.resolve() for game, path in iwads.items()}
    consumed.update({path: digest(path) for path in iwads.values()})
    rules, definitions = {}, {}
    for path in rule_paths:
        game, raw = read_rules(path)
        if game in rules:
            raise ValueError("Duplicate native network rules package")
        rules[game] = raw
        consumed[path] = digest(path)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            definitions[game] = json.loads(archive.read(f"{GAMES[game][0]}/apdoom_definitions.json"))
    if set(rules) != set(GAMES):
        raise ValueError("Supply both reviewed rules-only APWorlds")

    # Import the real server only after preflight. This is developer tooling,
    # not a dependency of the separately distributed player companion.
    import websockets
    from MultiServer import Context, LocationStore, NetworkItem, server

    context = Context("127.0.0.1", 0, None, None, 0, 0, True)
    context.load(str(room), False)
    slots = {game: [slot for slot, title in context.games.items() if title == record[1]] for game, record in GAMES.items()}
    if len(context.games) != 2 or any(len(matches) != 1 for matches in slots.values()):
        raise ValueError("Owned network fixture requires exactly one generated slot for each supported Doom game")
    placements = {slot: dict(locations) for slot, locations in context.locations.items()}
    for game in GAMES:
        slot = slots[game][0]
        items, map_placements = fixture_placements(definitions[game], placements[slot])
        options = context.slot_data[slot]
        if options.get("pistol_start") or not options.get("global_progressive_weapons") or options.get("vanilla_keys_mode"):
            raise ValueError("Owned fixture requires global progressive weapons, shuffled keys and carry saves")
        context.start_inventory[slot] = [NetworkItem(items[name], -2, 0) for name in [*INITIAL_ITEMS, "fixture_map_access"]]
        placements[slot].update({location: (item, slot, 0) for location, item in map_placements.items()})
    context.locations = LocationStore(placements)
    output.mkdir(parents=True)
    results = []
    handler = logging.FileHandler(output / "server.log", encoding="utf-8")
    logging.getLogger().addHandler(handler)
    try:
        assets = build_local_cache(list(iwads.values()), output / "asset-cache")
        async with websockets.serve(functools.partial(server, ctx=context), "127.0.0.1", 0) as listener:
            context.server = listener
            port = listener.sockets[0].getsockname()[1]
            for game in GAMES:
                slot = slots[game][0]
                player = context.player_names[0, slot]
                directory = output / game
                directory.mkdir()
                (directory / "games").mkdir()
                (directory / "APMine-private-network.marker").write_bytes(b"APMine-private-network-v1\n")
                shutil.copyfile(assets, directory / "BaseAssets.zip")
                for rule_game, raw in rules.items():
                    (directory / "games" / f"{GAMES[rule_game][0]}.apworld").write_bytes(raw)
                for phase in ("initial", "reconnect"):
                    results.append(await run_native(build / "bin/archipelago-doom.exe", prefix, iwads[game], directory,
                        game, player, port, phase, context, slot, definitions[game]))
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        if any(digest(path) != expected for path, expected in consumed.items()):
            raise ValueError("Owned network acceptance changed source/native/room/rules/IWAD inputs")
    return {"schema_version": 1, "status": "private-native-loopback-gameplay-acceptance", "release_ready": False,
            "published": False, "gameplay_diagnostics": True, "room_seed": context.seed_name, "runs": results,
            "consumed_inputs": [{"path": str(path), "sha256": expected} for path, expected in consumed.items()],
            "server_log_sha256": digest(output / "server.log"),
            "limitations": ["Generated test room reused in memory with controlled fixture placements/start items; no seed logic claim",
                            "Fresh ephemeral real AP server bound only to loopback, with no server-save loading/writing",
                            "Diagnostic fixture positions actors; release binaries do not include this automation",
                            "Audio disabled; native captures need separate visual inspection",
                            "Not source delivery, historical GPL remediation completion or resource permission"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "build", "library-prefix", "room", "doom-iwad", "doom2-iwad", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--apworld", type=Path, action="append", required=True)
    args = parser.parse_args()
    result = asyncio.run(collect(args.source, args.build, args.library_prefix, args.room,
        {"doom": args.doom_iwad, "doom2": args.doom2_iwad}, args.apworld, args.output))
    with (args.output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(f"Passed {len(result['runs'])} owned native loopback gameplay cases. Private evidence: {args.output}")
    print("No live room, public listener, player save or published package was used.")


if __name__ == "__main__":
    main()
