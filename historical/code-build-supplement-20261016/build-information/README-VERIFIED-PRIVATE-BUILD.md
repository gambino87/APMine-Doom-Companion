# Private historical native build review

This kit contains the unchanged v16 private historical source candidate, original
licenses/notices, and standalone build helpers. It is an incomplete review kit.
The source snapshot is
432888c88494af44b3a6fa2d5152dd6ff693d026b1797bd6b7f16ea0ac1fd3a0.
All original program, dependency, font and separately licensed art notices remain
attached. The helper code is GPL-3.0-or-later; COPYING.GPL3.txt is included.

Requirements: Windows x64, Python 3.12, Visual Studio 2022 C++ Build Tools with a
Windows SDK, CMake and Ninja. This local review uses C:\BuildTools, CMake
3.31.6-msvc6 and Ninja 1.12.1. The helpers accept a different BuildToolsRoot.

From this kit's root, build the supplied dependency sources into a separate new
directory (or use an unchanged installation already built by this helper from
the identical dependency manifest):

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\doom_companion\build_dependencies.ps1 -Bundle .\historical\dependencies -BuildRoot C:\private-build\dependencies -PythonExecutable C:\path\to\python.exe -BuildToolsRoot C:\BuildTools

The eight preferred source packages and their original build options, port
patches, notices and source-provenance records are included. The dependency
installation marker must match the supplied DEPENDENCY_SOURCES.json. The current
native review reuses the previously verified v4 independent dependency rebuild;
it does not claim a fresh dependency rebuild in this run.

Supply your existing private historical BaseAssets.zip separately. The helper
accepts only the recorded 268,642-byte archive with SHA-256
08d10f7e4819497e176814b1e0a4f1de46a3073b5b6ea9da6234ecf96405a170.
The archive and its WAD payloads are excluded from this source export. Their
permissions, preferred editing sources and historical distribution remain open.

Build into another new, separate directory:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\doom_companion\build_historical.ps1 -SourceRoot .\historical -BuildRoot C:\private-build\native -DependencyPrefix C:\private-build\dependencies\installed -PlayerAssets C:\your-private-files\BaseAssets.zip -PythonExecutable C:\path\to\python.exe -BuildToolsRoot C:\BuildTools

The helper verifies the pinned source snapshot, copies the engine into its own
working directory, and adds the supplied resource archive only to that copy.
It disables the inherited publication hooks, restricts dependency discovery to
the selected prefix, and builds archipelago-doom, apdoom-launcher, apdoom-setup
and APCpp. Generated embedded_files.c must match the recorded historical compiler
input exactly. Compiler source paths, native output headers, working source,
the original source kit, player input and dependency installation are checked.
No client is launched, installed for players, packaged or published.

The source's BUILD_DATE default remains __DATE__. Toolchain, link timestamps,
debug information, absolute build paths and independently rebuilt dependency
configurations can affect binary bytes. This check does not claim byte-identical
complete historical PE images; the earlier object/image correspondence evidence
remains a separate bounded result.

historical_build_settings.json preserves the selected recorded CMake cache
controls and their provenance hash. The original Release build intentionally
cleared its global compiler and linker flags. The wrapper applies those exact
values, and rejects ordinary optimized/dynamic-runtime Release defaults. The
configuration header and both generated embed files must also match the three
recorded historical input hashes before compilation starts.

Original ICO files are supplied for Windows resource compilation. Their retained
source-only recipe documents its alternate PNG encoding limits. The Archipelago
logo retains its original CC BY-NC 4.0 terms and attribution; aggregate compatibility
with the program's GPL terms remains unresolved. This kit does not establish a
complete corresponding-source offer, clearance of historic resources, recipient
source delivery/cure, or permission to restore retired downloads.
