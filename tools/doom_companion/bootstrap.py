"""Prepare player-local resources and open a separately installed Doom companion.

Private acceptance workflow, not a published companion installer or legal clearance.
No engine, asset or dependency download is performed.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from .inventory import DIAGNOSTIC_MARKERS, GAMEPLAY_DIAGNOSTIC_MARKER, embedded_zips, sha256
from . import local_assets
from .local_assets import build_local_cache, NATIVE_PICKUP_PROFILE, NATIVE_PICKUP_MARKER


GAMES = {"doom": ("doom_1993", "DOOM 1993", "DOOM.WAD"), "doom2": ("doom_ii", "DOOM II", "DOOM2.WAD")}
PYTHON_MEMBERS = {"__init__.py", "Items.py", "Locations.py", "Maps.py", "Options.py", "PickupRando.py", "Regions.py", "Rules.py"}
CORE_BINARIES = {"archipelago-doom.exe", "apdoom-launcher.exe", "apdoom-setup.exe", "apcpp.dll"}
RUNTIME_LIBRARIES = {"sdl2.dll", "sdl2_mixer.dll", "z.dll", "libpng16.dll", "samplerate.dll", "ogg.dll",
                     "vorbis.dll", "vorbisfile.dll", "wavpackdll.dll"}
MAX_RULE_BYTES = 8 * 1024 * 1024
MARKER = "APMine-player-runtime.json"


@dataclass(frozen=True)
class PlayerRuntime:
    directory: Path
    client: Path
    library_directories: tuple[Path, ...]
    iwads: dict[str, Path]


def private_root(path: Path, client: Path, libraries: list[Path]) -> Path:
    root = path.resolve()
    repository = Path(__file__).resolve().parents[2]
    if root.is_relative_to(repository) or any(root.is_relative_to(directory) or directory.is_relative_to(root)
                                              for directory in (client, *libraries)):
        raise ValueError("Player runtime must stay outside source and executable/library directories")
    for parent in (root, *root.parents):
        if (parent / ".git").exists() or (parent / "SOURCE_SNAPSHOT.json").is_file() or (parent / "prepare-manifest.json").is_file() or (
            (parent / "CMakeLists.txt").is_file() and (parent / "src/ap_basic.c").is_file()
        ):
            raise ValueError("Player runtime cannot be stored in companion source/build inputs")
    return root


def linked_binaries(client: Path, libraries: list[Path], source_link: Path) -> dict[str, Path]:
    record = json.loads(source_link.read_text(encoding="utf-8"))
    if record.get("schema_version") != 1 or record.get("status") != "private-binary-source-link" or not re.fullmatch(
        r"[0-9a-f]{64}", record.get("source_snapshot_sha256", "")
    ):
        raise ValueError("A reviewed private binary/source linkage record is required")
    if record.get("gameplay_diagnostics", False) is not False:
        raise ValueError("Player startup requires a normal client without gameplay diagnostics")
    binaries = {}
    for entry in record.get("binaries", []):
        name = entry.get("name", "")
        lower = name.lower()
        if lower not in CORE_BINARIES | RUNTIME_LIBRARIES or lower in binaries or Path(name).name != name:
            raise ValueError(f"Unreviewed or duplicate companion binary: {name}")
        candidates = [directory / name for directory in (client, *libraries) if (directory / name).is_file()]
        if len(candidates) != 1:
            raise ValueError(f"Missing or ambiguous separately installed binary: {name}")
        binary = candidates[0].resolve()
        raw = binary.read_bytes()
        if sha256(raw) != entry.get("sha256") or raw[:2] != b"MZ":
            raise ValueError(f"Companion binary no longer matches the reviewed source record: {name}")
        if list(embedded_zips(raw)):
            raise ValueError(f"Companion binary contains an unreviewed embedded archive: {name}")
        if any(marker in raw for marker in DIAGNOSTIC_MARKERS):
            raise ValueError(f"Player startup rejects a gameplay diagnostic binary: {name}")
        binaries[lower] = binary
    if set(binaries) != CORE_BINARIES | RUNTIME_LIBRARIES:
        raise ValueError("Source record must cover all four companion targets and nine runtime libraries")
    if any(binaries[name].parent != client for name in CORE_BINARIES):
        raise ValueError("All native companion targets must be in the selected client directory")
    if "pickup_sprite_profile" in record:
        if (record["pickup_sprite_profile"] != NATIVE_PICKUP_PROFILE
                or record.get("asset_recipe_sha256") != sha256(Path(local_assets.__file__).read_bytes())
                or NATIVE_PICKUP_MARKER not in binaries["archipelago-doom.exe"].read_bytes()):
            raise ValueError("Native pickup profile requires its matching renderer and asset recipe")
    system_libraries = {name.lower() for name in record.get("system_library_candidates", [])}
    for directory in (client, *libraries):
        for path in directory.iterdir():
            if path.name.lower() in system_libraries:
                raise ValueError(f"Unreviewed local system/CRT library shadows the source record: {path.name}")
            # A developer library prefix may contain unused codec DLLs. They are
            # not import inputs, copied into the runtime, or released by this tool.
            if directory == client and path.suffix.lower() in (".dll", ".exe") and path.name.lower() not in binaries:
                raise ValueError(f"Unrecorded native binary in the selected installation: {path.name}")
    return binaries


def read_rules(path: Path) -> tuple[str, bytes]:
    raw = path.read_bytes()
    if len(raw) > MAX_RULE_BYTES:
        raise ValueError("Selected rules package exceeds the supported size limit")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_RULE_BYTES:
            raise ValueError("Expanded rules package exceeds the supported size limit")
        names = archive.namelist()
        manifests = [name for name in names if name.endswith("/archipelago.json")]
        if len(manifests) != 1 or len(names) != len(set(names)):
            raise ValueError("Rules package must have exactly one game manifest and no duplicate members")
        manifest = json.loads(archive.read(manifests[0]))
        game = manifest.get("__apdoom", {}).get("short_name")
        if game not in GAMES:
            raise ValueError("Only reviewed Doom 1993 and Doom II rules packages are supported")
        module, title, iwad = GAMES[game]
        required = PYTHON_MEMBERS | {"archipelago.json", "apdoom_definitions.json", "LICENSE.txt",
                                     "THIRD_PARTY_NOTICES.txt", "docs/setup_en.md", f"docs/en_{title}.md"}
        if set(names) != {f"{module}/{name}" for name in required}:
            raise ValueError("Select a rules-only APWorld, with notices and no artwork/native payloads")
        metadata = manifest["__apdoom"]
        if manifest.get("game") != title or metadata.get("iwad") != iwad or metadata.get("definitions") != f"{module}/apdoom_definitions.json":
            raise ValueError("Rules manifest has inconsistent game/IWAD/definition identity")
        for name in names:
            text = archive.read(name).decode("utf-8")
            if "\0" in text:
                raise ValueError("Rules-only packages must not contain binary data")
            if name.endswith(".py"):
                ast.parse(text, filename=name)
            if name.endswith(".json") and not isinstance(json.loads(text), dict):
                raise ValueError("Rules JSON members must be objects")
            if name.endswith(("/LICENSE.txt", "/THIRD_PARTY_NOTICES.txt")) and not text.strip():
                raise ValueError("Rules license notices must not be empty")
    return game, raw


def managed_path(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or any(part in (".", "..") for part in name.replace("\\", "/").split("/")) or ":" in name:
        raise ValueError("Unsafe managed runtime path")
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Managed runtime path escapes the private profile")
    for current in (path, *path.parents):
        if current == root.parent:
            break
        if current.is_symlink():
            raise ValueError("Player runtime must not follow symlinks")
    return path


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".apmine-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_runtime(client: Path, libraries: list[Path], source_link: Path, iwads: list[Path],
                    rules: list[Path], state_root: Path) -> PlayerRuntime:
    client, libraries = client.resolve(), [path.resolve() for path in libraries]
    binaries = linked_binaries(client, libraries, source_link)
    root = private_root(state_root, client, libraries)
    selected = {}
    payloads = {}
    for path in rules:
        game, raw = read_rules(path)
        if game in selected:
            raise ValueError("Select one rules-only APWorld per game")
        selected[game] = raw
        payloads[f"games/{GAMES[game][0]}.apworld"] = raw
    if not selected:
        raise ValueError("At least one rules-only APWorld is required")
    native_pickups = json.loads(source_link.read_text()).get("pickup_sprite_profile") == NATIVE_PICKUP_PROFILE
    cache = build_local_cache(iwads, managed_path(root, "assets"), native_pickups=native_pickups)
    assets = cache.read_bytes()
    with zipfile.ZipFile(io.BytesIO(assets)) as archive:
        provenance = json.loads(archive.read("APMine-local-assets.json"))
    supported = provenance["supported_games"]
    if set(supported) != set(selected):
        raise ValueError("Select exactly one matching original IWAD and rules APWorld for each supported game")
    source_paths = {sha256(path.read_bytes()): path.resolve() for path in iwads}
    game_iwads = {entry["game"]: source_paths[entry["sha256"]] for entry in provenance["sources"]}
    # Client/rules/recipe updates and adding the other game retain player saves.
    profile = managed_path(root, "runtime")
    profile.mkdir(parents=True, exist_ok=True)
    marker = managed_path(profile, MARKER)
    previous = json.loads(marker.read_text()) if marker.is_file() else None
    allowed_managed = {"BaseAssets.zip", *(f"games/{identity[0]}.apworld" for identity in GAMES.values())}
    if previous is not None and (previous.get("schema_version") != 1 or previous.get("private_cache") is not True
                                 or not isinstance(previous.get("managed"), dict)
                                 or not set(previous["managed"]) <= allowed_managed):
        raise ValueError("Existing profile has inconsistent ownership metadata")
    payloads["BaseAssets.zip"] = assets
    expected = {name: sha256(raw) for name, raw in payloads.items()}
    removed = set(previous["managed"]) - set(expected) if previous else set()
    for path in profile.iterdir():
        if path.suffix.lower() in (".dll", ".exe"):
            raise ValueError("Private runtime contains a native binary that could shadow the verified installation")
    games_directory = managed_path(profile, "games")
    if games_directory.exists():
        for path in games_directory.iterdir():
            if path.suffix.lower() == ".apworld" and f"games/{path.name}" not in set(expected) | removed:
                raise ValueError("Private runtime contains an unowned APWorld; no extra worlds were installed or removed")
    for name in removed:
        target = managed_path(profile, name)
        if target.exists() and sha256(target.read_bytes()) != previous["managed"][name]:
            raise ValueError(f"Previously managed runtime file was modified: {name}; it was not removed")
    for name, raw in payloads.items():
        target = managed_path(profile, name)
        if not target.exists():
            continue
        old = target.read_bytes()
        if sha256(old) == expected[name]:
            continue
        if previous is None or previous.get("managed", {}).get(name) != sha256(old):
            raise ValueError(f"Existing runtime file is unowned or was modified: {name}; it was not overwritten")
    for name, raw in payloads.items():
        target = managed_path(profile, name)
        if target.exists():
            old = target.read_bytes()
            if old == raw:
                continue
            backup = managed_path(profile, f"history/{sha256(old)}/{name}")
            if backup.exists() and backup.read_bytes() != old:
                raise ValueError("Existing private backup differs; no managed file was replaced")
            if not backup.exists():
                atomic_write(backup, old)
        atomic_write(target, raw)
    for name in removed:
        target = managed_path(profile, name)
        if not target.exists():
            continue
        old = target.read_bytes()
        backup = managed_path(profile, f"history/{sha256(old)}/{name}")
        if backup.exists() and backup.read_bytes() != old:
            raise ValueError("Existing private backup differs; no managed file was removed")
        if not backup.exists():
            atomic_write(backup, old)
        target.unlink()
    record = {"schema_version": 1, "private_cache": True, "release_ready": False, "sources": provenance["sources"],
              "managed": expected, "binary_source_link_sha256": sha256(source_link.read_bytes())}
    atomic_write(marker, (json.dumps(record, indent=2) + "\n").encode())
    return PlayerRuntime(profile, binaries["apdoom-launcher.exe"], tuple(libraries), game_iwads)


def launch(runtime: PlayerRuntime, game: str | None = None, practice: bool = False,
           server: str | None = None, player: str | None = None, smoke_test: bool = False,
           launcher_smoke_game: str | None = None, launcher_smoke_launch: bool = False) -> subprocess.Popen:
    if launcher_smoke_game is not None or launcher_smoke_launch:
        if not smoke_test or game is not None or launcher_smoke_game not in runtime.iwads or practice or server or player:
            raise ValueError("Launcher scenarios require a finite GUI check, a prepared game, and no network/direct-game options")
    if game is not None and game not in runtime.iwads:
        raise ValueError("Selected game has no prepared player IWAD/rules")
    if (practice or server or player) and game is None:
        raise ValueError("Practice/network launch requires an explicit game")
    if bool(server) != bool(player) or (practice and server) or (smoke_test and game is not None and not practice):
        raise ValueError("Use practice mode or both room address and slot name; game smoke checks require practice mode")
    if game is not None and not practice and not server:
        raise ValueError("Direct game launch requires practice mode or a room address and slot name")
    if game is None and any(path.name.upper() != GAMES[name][2] for name, path in runtime.iwads.items()):
        raise ValueError("The native game chooser requires original DOOM.WAD/DOOM2.WAD filenames; use a direct game launch for renamed files")
    arguments = [str(runtime.client.parent / "archipelago-doom.exe" if game else runtime.client)]
    if game:
        arguments.extend(("-game", game, "-iwad", str(runtime.iwads[game])))
    if practice:
        arguments.append("-practice")
    if server:
        arguments.extend(("-apserver", server, "-applayer", player))
    if smoke_test:
        arguments.extend(("-apmine-smoke-test", "-nosound"))
    if launcher_smoke_game:
        arguments.extend(("-apmine-smoke-game", launcher_smoke_game))
    if launcher_smoke_launch:
        arguments.append("-apmine-smoke-launch")
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join([str(runtime.client.parent), *(str(path) for path in runtime.library_directories), environment.get("PATH", "")])
    environment["DOOMWADPATH"] = os.pathsep.join(str(path) for path in runtime.iwads.values())
    if smoke_test:
        environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SDL_RENDER_DRIVER="software")
    stdout = (runtime.directory / "client-stdout.log").open("ab")
    stderr = (runtime.directory / "client-stderr.log").open("ab")
    try:
        return subprocess.Popen(arguments, cwd=runtime.directory, env=environment, stdout=stdout, stderr=stderr,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" and smoke_test else 0)
    finally:
        stdout.close()
        stderr.close()


def default_state_root() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "APMine/DoomCompanion"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "apmine/doom-companion"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-dir", type=Path, required=True)
    parser.add_argument("--library-dir", type=Path, action="append", default=[])
    parser.add_argument("--source-link", type=Path, required=True)
    parser.add_argument("--iwad", type=Path, action="append", required=True)
    parser.add_argument("--apworld", type=Path, action="append", required=True)
    parser.add_argument("--state-root", type=Path, default=default_state_root())
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--game", choices=GAMES)
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--server")
    parser.add_argument("--player")
    parser.add_argument("--smoke-test", action="store_true", help="Finite private launcher or practice rendering check, without sound")
    parser.add_argument("--launcher-smoke-game", choices=GAMES, help="Select a game through finite offline GUI input")
    parser.add_argument("--launcher-smoke-launch", action="store_true", help="Also start and wait for its finite practice child")
    args = parser.parse_args()
    runtime = prepare_runtime(args.client_dir, args.library_dir, args.source_link, args.iwad, args.apworld, args.state_root)
    print(f"Private player runtime: {runtime.directory}")
    print("Player-derived resources stay local. This is not a published companion release.")
    if not args.prepare_only:
        stdout = runtime.directory / "client-stdout.log"
        old_size = stdout.stat().st_size if stdout.exists() else 0
        process = launch(runtime, args.game, args.practice, args.server, args.player, args.smoke_test,
                         args.launcher_smoke_game, args.launcher_smoke_launch)
        if args.smoke_test:
            try:
                code = process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("Owned finite smoke client timed out; private logs retained")
            if code:
                raise RuntimeError(f"Owned finite smoke client failed: exit {code}; private logs retained")
            with stdout.open("rb") as stream:
                stream.seek(old_size)
                expected = b"APMINE_SMOKE: captured rendered frame" if args.game else b"APMINE_LAUNCHER_SMOKE: captured "
                fresh = stream.read()
                if expected not in fresh:
                    raise RuntimeError("Finite client did not report a fresh rendered capture; private logs retained")
                if args.launcher_smoke_game and f"selected {args.launcher_smoke_game} through SDL menu input.".encode() not in fresh:
                    raise RuntimeError("Finite launcher did not verify the requested game selection")
                if args.launcher_smoke_launch and f"child {args.launcher_smoke_game} initialized and exited successfully.".encode() not in fresh:
                    raise RuntimeError("Finite launcher did not verify its practice child's lifecycle")
            print("Finite client reported a fresh rendered capture; inspect it before claiming visual acceptance.")


if __name__ == "__main__":
    main()
