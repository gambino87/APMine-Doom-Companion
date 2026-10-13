# APMine Doom Companion

Private source repository candidate for the separate Doom 1993 / Doom II client.
This directory has not been published and is not an approved release. It is not
verified corresponding source for the previously distributed APDoom binaries.

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

The combined engine/APCpp build uses GPL version 3; this does not remove the
component grants or relicense original game data. See [GPLv3](COPYING.GPL3.txt),
[engine GPLv2 text](COPYING.md), and the retained component notices. There is
no warranty. This candidate does not claim permission to distribute Doom IWADs,
Scythe, Brutal Doom, extracted artwork, or other third-party mods.

Before a public repository or binary release is authorized, verify the complete
preferred source/build inputs for each shipped native dependency, source/art
provenance, clean builds, live AP item/save behavior, and recipient source access.
Every eventual binary release needs an immutable matching source revision and
build record. A moving branch or upstream homepage is not a substitute for
recovering the source of the retired releases.
