# Third-Party Notices

These notices describe the prepared candidate, not clearance for historical
downloads. Component copyright/license notices in source files are retained.
The candidate's source input hashes are in `SOURCE_SNAPSHOT.json`.

## Engine And Networking Source

- APDoom and its Doom/Boom/PrBoom/Chocolate/Crispy lineage: GPL-2.0-or-later,
  original notices in `COPYING.md`, `README.upstream.md` and source headers.
  https://github.com/ArchipelagoDoom/APDoom
- APCpp: GPLv3, full notice in `APCpp/LICENSE`. Recovered dependency baseline
  `c12ee5dd013eebb5f0e239f8256663007174adae`, with local modifications; this is
  not an assertion that every copied file equals that upstream revision.
  https://github.com/Daivuk/APCpp
- IXWebSocket: BSD-3-Clause, `APCpp/IXWebSocket/LICENSE.txt`; recovered baseline
  `86bc52ee2eacec63cfca8f16284298eff88cfbd1`.
  https://github.com/machinezone/IXWebSocket
- JsonCpp: retained dual public-domain/MIT notice in `APCpp/jsoncpp/LICENSE`;
  recovered baseline `8190e061bc2d95da37479a638aa2c9e483e58ec6`.
  https://github.com/open-source-parsers/jsoncpp
- mbedTLS 3.4.0: supplied preferred source archive
  `APCpp/mbedtls-3.4.0.tar.gz`, including its upstream LICENSE and nested notices.
  https://github.com/Mbed-TLS/mbedtls/tree/mbedtls-3.4.0
- IXWebSocket's nested cpp-linenoise notice remains in
  `APCpp/IXWebSocket/third_party/cpp-linenoise/LICENSE`.

The parent APDoom source was recovered from an unversioned local working copy.
Do not invent an upstream revision or equate this modified candidate with the
source of a retired binary. The local remediation changes are recorded against
the recovered preparation baseline, separately from older APMine gameplay edits.

## Native Build Dependencies

Unmodified installed-library copyright files are copied to
`licenses/dependencies`. `DEPENDENCIES.json` records source/port URLs and versions
from that installation's SPDX records. This includes installed optional libraries
that may not be linked; a release must inventory its actual dependencies.
For a candidate assembled with `--dependency-sources`, the required preferred
library sources and exact build/patch recipes are present under
`vendor/native-dependencies`. Its hash-bound manifest distinguishes linked
libraries from unused installed dependencies. Source presence in a private
candidate is not public source delivery or historical release matching.
Without that option, the candidate contains references only, not library source.

In particular, the old upstream README's GPL description of libsamplerate does
not match the installed 0.2.2 dependency's BSD-2-Clause notice. Use the preserved
version-specific notice, not that outdated README line. Preserve any LGPL and
other dependency obligations if those libraries are actually distributed.

## Local Reconstruction Helpers

The APMine local reconstruction, source verification and build helpers are
carried from the GPLv3 platform repository. They do not incorporate IWAD artwork
or the old BaseAssets archive. Executing the recipe on player-owned data does
not grant permission to redistribute its generated artwork cache.

## Game Data And Release Access

No Doom/Heretic/Hexen/Strife IWADs, Scythe WAD, Brutal Doom mod, game-themed
icons, embedded asset archives, or rendered player caches are included.
Code licenses do not cover those game/mod assets. Other games are not claimed
as supported by this candidate merely because inherited engine code remains.

This source tree is prepared for publication at
https://github.com/gambino87/APMine-Doom-Companion under GPLv3 for the combined
engine/APCpp program. The standard `LICENSE` is an exact copy of
`COPYING.GPL3.txt`; it does not replace the retained component grants. No binary
package is included. Before binary distribution, provide recipients with
matching preferred source, build/run scripts and the applicable notices for
that actual release. The retired distribution's source and asset-permission
gaps remain unresolved. Publication documentation updated 2026-10-12.
