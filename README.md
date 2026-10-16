# APMine Doom Companion

Modified APDoom source for a separate Archipelago DOOM 1993 / DOOM II client.
This is a source publication, not a binary release or an installer. It is not
verified corresponding source for the previously distributed APDoom binaries.

## License

The combined engine/APCpp program is distributed under the **GNU General
Public License, version 3 (GPL-3.0-only)**. See [LICENSE](LICENSE) for the complete
license. You may copy, modify and redistribute the covered program under those
terms. There is no warranty.

Original component copyright and license grants remain intact. The engine's
GPL-2.0-or-later notices permit the combined GPLv3 build; APCpp supplies GPLv3
code. Separately licensed dependencies retain their BSD, MIT, Apache and other
applicable terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md),
[COPYING.md](COPYING.md), [COPYING.GPL3.txt](COPYING.GPL3.txt) and the notices in
each component. These code licenses do not license original game/mod data.

## Source And Build

The engine retains APMine's custom AP gameplay behavior. The platform distributes
the rules-only APWorlds separately; no platform build/publish hooks are included.
Embedded resource archives and game-themed program icons are disabled. Graphics
used while playing are reconstructed locally from the player's explicitly
selected, legitimately obtained original DOS IWADs. The resulting private cache
must never be committed, packaged or uploaded.

See [BUILDING.md](BUILDING.md) for the current build and local asset setup,
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution, and
[DEPENDENCIES.json](DEPENDENCIES.json) for dependency-source status. When staged
with a verified dependency bundle, preferred library sources, patches, notices
and configuration records are included under `vendor/native-dependencies`.
Candidates staged without that bundle contain only references, not those sources.
The engine's original notices and [upstream README](README.upstream.md) are
preserved. Do not follow the upstream instructions to embed APWorlds/assets.

Files changed during companion preparation carry prominent dated notices. Their
dates and before/after hashes are recorded in `SOURCE_SNAPSHOT.json`, which also
distinguishes newly authored native headers from recovered, unchanged dependency
source. The recovered baseline already contains older local gameplay edits;
their original authorship and dates have not been established. The preparation
notices do not invent those dates or provide historical license clearance.

## Game Resources

No Doom IWADs, Scythe WAD, Brutal Doom mod, extracted original-game artwork,
embedded resource archives or player caches are included. Supply your own
legitimately obtained supported original DOS IWADs, following the local resource
instructions in [BUILDING.md](BUILDING.md). Generated resources stay private;
do not upload them or add them to a release. Scythe and other third-party mods
are not supported or cleared for distribution by this source publication.

## Verification And History

The initial import at
[`c052864b80182a7191f8b3a822676397c99087b1`](https://github.com/gambino87/APMine-Doom-Companion/tree/c052864b80182a7191f8b3a822676397c99087b1)
preserves all 5,143 inventoried inputs from the independently built normal
companion snapshot. Its snapshot SHA-256 is
`57238b6c6a62c6678fa253ee6228b2fda41298c47f6e0a4a4bb806469123cce8`.
The publication documentation and standard license filename are subsequent,
source-inventoried changes; the native code and build recipes are unchanged.

`SOURCE_SNAPSHOT.json` and `DEPENDENCIES.json` retain their preparation-time
labels, not a live statement about this repository's visibility. The public
documentation update is recorded separately in the snapshot. Verification
does not make a binary release ready or establish continued source availability.

Every eventual binary release needs its own immutable matching source revision,
notices and build record. This new modified source does not resolve the retired
releases' corresponding-source gaps or establish historical GPL cure or
reinstatement. A newer build cannot substitute for an older recipient's actual
source. No binary download or platform deployment is part of this publication.

Initial publication documentation updated 2026-10-12.

## Source Repair Prepared 2026-10-15

This revision restores the original textscreen font notices, the executable
font converter and its three hash-reviewed PNG source inputs. Their original
DOSBox, Thomas A. Fine and Simon Howard license context is retained. It also
preserves the missing BSD notice for the vendored Python websockets protocol.
See THIRD_PARTY_NOTICES.md and the component files for the original terms.

The independent JSON_checker payloads are excluded from new source exports
while their compatible redistribution terms remain unresolved. Their original
attribution and test harness remain. JsonCpp's own 123 reviewed test-data files
are restored under its retained license. Optional tests requiring external
fixtures need separately supplied inputs; BUILDING.md documents that boundary.

Preparation and source-access checks enforce those inputs and notices. The
native C/C++ code, CMake/compiler recipes and existing generated font headers
are unchanged from the preceding public revision. CMake uses those headers;
the retained optional Autotools font rules can regenerate them when enabled.
This source repair does not establish historical source delivery or license
cure, authorize a binary release, or license original game/mod resources.
