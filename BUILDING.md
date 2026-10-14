# Building The Source

These instructions build a local proof of concept, not a redistributable package.
No signing, deployment, binary download, or asset download is performed.

The source publication is licensed as described in `README.md` and `LICENSE`.
Preparation-time "private candidate" labels below and in the hash inventories
are historical technical records, not restrictions on the GPL-granted rights
to copy, modify or redistribute covered code. The first import commit referenced
in `README.md` is the exact preferred-source snapshot for the recorded normal
build; a later binary must have its own matching source/build record. Publication
documentation updated 2026-10-12; native source and recipes are unchanged.

## Requirements

- Python 3.12 or later for source verification, archive proofs and the complete
  build/test workflow. Player-local asset construction alone supports Python 3.10.
- Visual Studio 2022 C++ build tools, Windows SDK, CMake and Ninja.
- The staged native dependency sources in `vendor/native-dependencies`, when
  `DEPENDENCIES.json` reports `required_dependency_sources_present: true`.
  They provide SDL2, SDL2_mixer, zlib, libpng, libsamplerate, Ogg, Vorbis and
  WavPack plus the recorded port/CMake helpers. Optional FluidSynth and SDL2_net
  are not linked by this profile. A references-only candidate instead requires
  a separately obtained library prefix and is not a complete source candidate.
- The engine/APCpp/IXWebSocket/JsonCpp source and mbedTLS source archive already
  included in this candidate. Do not substitute the old embedded-art binaries.
- Separately obtained, reviewed rules-only `doom_1993.apworld` and
  `doom_ii.apworld` files for engine game definitions.
- The player's original DOS Ultimate Doom and/or Doom II IWADs for playing.
  These are not compiler inputs and must stay outside the source/build trees.

The retained `vcpkg.json` records the original dependency baseline. The local
source build below does not download that baseline or use the old binary prefix.
Do not silently substitute a newer installed library set for a matched release.

## Verify And Build

From this candidate directory in Developer PowerShell (so `dumpbin.exe` is on PATH):

```powershell
python -m tools.doom_companion.source_repository --verify .
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/doom_companion/build_dependencies.ps1 -Bundle .\vendor\native-dependencies -BuildRoot "C:\apdoom-build\dependencies" -PythonExecutable "C:\Python312\python.exe" -BuildToolsRoot "C:\BuildTools"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/doom_companion/build_local.ps1 -SourceDir . -BuildDir "C:\apdoom-build\native" -LibraryPrefix "C:\apdoom-build\dependencies\installed" -BuildToolsRoot "C:\BuildTools"
python -m unittest discover -s tests -p "test_*.py"
python -m tools.doom_companion.release_source_link --source . --build "C:\apdoom-build\native" --library-prefix "C:\apdoom-build\dependencies\installed" --dumpbin (Get-Command dumpbin.exe).Source --output "C:\apdoom-build\SOURCE_LINK.json"
```

Adjust Python/build-tool paths to the developer's installation and keep the
dependency output outside the source checkout. This recipe does not require a
player IWAD or the original dependency cache. The local
helper never copies DLLs into a release or installs the
client. It configures a Release/Ninja build and builds `archipelago-doom`,
`apdoom-launcher`, `apdoom-setup` and their APCpp dependency in the chosen native
directory (`build-local` inside the checkout only if `-BuildDir` is omitted).
CMake's generated Windows resource files are not preferred source inputs.

The companion source snapshot hashes must still match after the build. Changes
to actual source require a new reviewed snapshot before release preparation.
This snapshot is a preparation record, not a public release tag.

The source-link command checks the actual native dependency closure, source and
library build directories and hashes. Its report supplies the private bootstrap
below. This check is technical build evidence, not cryptographic attestation,
public source delivery or permission to redistribute every system/CRT library.
The helper and its tests are included here; no platform checkout is needed.

## Verify Public Source Access

After publication has been explicitly authorized and a reviewed source candidate
has been published in the separate public repository, verify a full immutable
commit rather than a moving branch, tag or repository homepage:

```powershell
python -m tools.doom_companion.source_access --source . --source-link "C:\apdoom-build\SOURCE_LINK.json" --repository "OWNER/REPOSITORY" --commit "FULL_40_CHARACTER_COMMIT_SHA" --output "C:\apdoom-build\SOURCE_ACCESS.json"
```

This command does not publish, sign, package or modify anything on GitHub. It
downloads the commit archive through GitHub's public archive API without user
credentials/cookies/proxies, bounds ZIP expansion, refuses unsafe members and
requires every preferred source/dependency/notice/build-script byte to match the
reviewed snapshot and its normal thirteen-binary source record. Extra payloads,
missing inputs, modified notices, mutable refs and login/404 responses fail.

Keep the resulting hash-bound access record with the exact eventual binary
release; never present a newer candidate as the source for an older release.
The snapshot retains its preparation-time status rather than being rewritten.
Preserve its exact bytes during import into Git; line-ending normalization or
LFS pointers will fail the check. An import that changes source needs a newly
reviewed snapshot and matching build record, not a relaxed hash comparison.
The record is source-access evidence at that time, not continued hosting, a
cryptographic build attestation, historical GPL cure/reinstatement, original
asset rights or general release clearance. If no reviewed public commit exists,
this step is still incomplete; do not manufacture a successful record.
GitHub API documentation:
https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip.

## Player-Local Resources

This candidate adds `native-composite-v1` pickup rendering. Its source-linked
bootstrap selects the matching resource profile automatically; do not mix its
transparent sprite-reference placeholders with an older engine. The renderer
reads the player's loaded WAD sprite frames, preserves their ground anchors,
and builds the filler dot, useful plus and progression chevrons in memory at
startup. Notifications and the Energy Link menu share that sprite provider.
World pickup categories use the server's scouted item flags, not the vanilla
pickup's name. These symbols are newly authored source code, not the AP logo.
No composed pickup image is written back to the IWAD or included in a release.
DOOM's absent MegaSphere frames use only our original standalone symbol;
DOOM II uses its player-supplied MegaSphere frames. No cross-game art is needed.
The older source-link profile still uses private local aliases for compatibility.

```powershell
python -m tools.doom_companion.local_assets --iwad "C:\Games\DOOM\DOOM.WAD" --iwad "C:\Games\DOOM\DOOM2.WAD" --cache-root "$env:LOCALAPPDATA\APMine\DoomAssets"
```

Selecting only one supported IWAD is also allowed. The command prints a private
cache directory. Do not copy this cache into the source or executable build
directory. The private bootstrap combines that construction, reviewed rules
installation and native launcher startup without requiring manual file copying:

```powershell
python -m tools.doom_companion.bootstrap --client-dir "C:\apdoom-build\native\bin" --library-dir "C:\apdoom-build\dependencies\installed\bin" --source-link "C:\apdoom-build\SOURCE_LINK.json" --iwad "C:\Games\DOOM\DOOM.WAD" --iwad "C:\Games\DOOM\DOOM2.WAD" --apworld "C:\Downloads\doom_1993.apworld" --apworld "C:\Downloads\doom_ii.apworld"
```

Adjust the compiled client directory to the chosen native build. SOURCE_LINK.json
must be the hash-verified private binary/source record for that build, not a
record from a different build or the retired embedded-art executables. This is
still developer acceptance tooling; it does not grant binary redistribution or
public source access. A published installer and source-release workflow remain
pending. No tools, dependencies or assets are downloaded by the bootstrap.

Supply only the matching IWAD/APWorld pairs for the games you own. Without
`--game`, the existing native game/connection chooser opens. Direct launches use
`--game doom` or `--game doom2` plus `--practice`, or both `--server` and
`--player`. `--prepare-only` constructs/validates the private runtime without
opening a client. `--smoke-test` captures the native chooser and exits;
`--smoke-test --game doom2 --practice` captures an engine title frame. Both are
finite, sound-disabled rendering checks, not gameplay/audio acceptance. Never connect an acceptance
client to a player's live room without authorization.

For finite native-menu interaction checks, add `--launcher-smoke-game doom` or
`doom2` alongside `--smoke-test`, without direct-game/network options. This uses
SDL keyboard events to select the requested game in the practice menu. Add
`--launcher-smoke-launch` to also start and wait for its finite offline practice
child. The launcher verifies selection and the child initialization/exit result
before capture. These switches cannot start a network game and do not replace
in-level item/key/exit/save or audible-audio acceptance.

## Private Offline Gameplay Acceptance

The optional `-GameplayDiagnostics` build-helper switch enables the native
offline gameplay fixture. The ordinary build explicitly sets the CMake option
`APMINE_GAMEPLAY_DIAGNOSTICS` off, including when reusing a diagnostic build
directory. Never publish the diagnostic executable as a player release.

```powershell
python -m tools.doom_companion.gameplay --source "C:\apdoom-source" --build "C:\apdoom-build\native" --library-prefix "C:\apdoom-build\dependencies\installed" --doom-iwad "C:\Games\DOOM\DOOM.WAD" --doom2-iwad "C:\Games\DOOM\DOOM2.WAD" --apworld "C:\Downloads\doom_1993.apworld" --apworld "C:\Downloads\doom_ii.apworld" --output "$env:LOCALAPPDATA\APMine\DoomAcceptance\new-private-case"
```

This runner accepts only a reviewed prepared source, matching tested native
headers, a diagnostic CMake configuration, two rules-only APWorlds and explicit
original IWADs. Each case runs in a new marked, dummy-SDL, offline practice
directory with monsters and sound disabled. Only its owned child is killed on
timeout/interruption; inputs are hash-checked even on failure. Logs, captures,
locally reconstructed assets and any retained diagnostic binaries stay private.

The six cases touch actual spawned AP pickups and keys, then exercise native
level-select save/return, fresh pistol-start reset, pistol-resume restoration
and completion state for Doom and Doom II. The fixture moves its player and
changes health/angle to distinguish actual save restoration from cached AP
state. Completion is separate from the ordinary pickup-index ledger. This is
not a network `LocationChecks` or audible-audio test, nor corresponding-source
publication or resource-permission clearance. Inspect captures separately.

`tools.doom_companion.network_gameplay` provides a separate developer-only
loopback network fixture using the same diagnostic build. It additionally needs
an independently installed Archipelago Python checkout exposing `MultiServer`
and a generated, private two-slot room containing one DOOM 1993 and one DOOM II
player. That server checkout is not needed to build or play the companion. Its
regression tests use stubs and remain runnable without the platform checkout.

The runner loads only the supplied room's generation data, not its save. It
starts a fresh real AP server on `127.0.0.1` with an OS-assigned port, substitutes
controlled second-map placements/start items only in that private server's
memory, and redirects each client's native/AP saves to the fresh test directory.
Both games touch spawned checks, receive keys/upgrades, save/return, exit and
reconnect without duplicate grants. Only owned subprocesses are reaped on
timeout/interruption; source/binary/room/rules/IWAD inputs are hash-checked even
on failure. The fixture is not a seed-solvability, arbitrary-option, live-room
or audio acceptance test. It never listens publicly or writes the supplied
room/server save, and requires private Windows LOCALAPPDATA/APMine output.

Rebuild with diagnostics off afterward. Player/source-link gates reject both
offline and network diagnostic markers even if a cached build flag says off.

The default state is under LOCALAPPDATA/APMine/DoomCompanion on Windows, or the
user's XDG data directory on Linux. Native saves/connection settings remain in
the stable runtime directory across recipe/rules/client updates and adding the
other game. Replaced managed resource/rule files receive content-addressed
private backups. Unexpected user modifications are never overwritten or removed.
The generated runtime/cache must not be uploaded or packaged.

The new local recipe does not read the retired BaseAssets archive. The original
IWADs, generated resource ZIP/WADs, screenshots, saves and credentials remain
private. Audio, native GUI interaction, every gameplay option, live AP receipts and save/reload
are separate acceptance checks, not covered by the asset codec unit tests.
