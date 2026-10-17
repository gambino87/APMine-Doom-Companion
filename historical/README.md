# Historical code/build source supplement

This directory adds historical source information alongside the supported
companion. It does not change the supported engine, build, bootstrap or client.
The pinned public base is c3aa08e2dfefe100397269fee964968653792dbd.

The [frozen supplement](code-build-supplement-20261016/REVIEW.md) preserves forty
exact historical code/build files and their original notices, plus recorded build
controls, a resource-recovery helper and bounded comparison evidence. Its
SUPPLEMENT_INPUTS.json lists fifty-two payload identities; the manifest itself
is the fifty-third file. All fifty-three are retained byte for byte from the
verified private archive, whose SHA-256 is
d3a8bc474f49e18a52753c57f001dfeb1716f1ff45355f4127158409017fec7c.
The preparation-time local/publication labels in that frozen record describe
the original checkpoint, not this directory's eventual visibility.

The compiler audit observed 723 distinct engine/TLS source/header inputs: 707
exact bodies are in the pinned public source (including its Mbed TLS archive),
and this supplement adds fourteen further observed code/header bodies. Two
observed icon arrays and other resource/source boundaries remain unresolved.
The forty historical files also preserve build controls and platform recipes;
their presence does not mean every recipe was compiled in the bounded audit.

Review engine-overlay/<path> against <path> in the pinned base. If applying an
overlay, use a separate disposable copy. These historical files must not replace
the supported engine by default. Original release scripts are preserved as source
information; inherited publication hooks must remain disabled in private builds.

The recorded private build instructions refer to a larger review kit. This
code-only supplement is not a standalone historical build kit or a complete
corresponding-source offer. It does not supply or license game/mod graphics,
audio, WADs, icon/resource arrays, embedded resource archives or native binaries.
Original component grants remain separate and unchanged; no blanket license
is applied to historical material or resources.

Recipients who already have a matching old companion executable can inspect the
separately GPL-licensed resource-recovery helper. It reads that existing file
without executing it and rejects anything without the exact known embedded
archive. For example, from the source repository root:

    python historical/code-build-supplement-20261016/build-information/recover_historical_assets.py --binary <existing-historical-exe> --output-directory <new-private-directory>

The output stays private. Recovery grants no resource redistribution rights and
does not establish preferred editing sources for those resources. The original
helper's full license and preserved build/control records are alongside it.
The exported helper already passed the retained thirteen-image checks on both
hosts; this supplement does not claim a fresh native compile or gameplay test.

Publishing code does not establish historical recipient delivery, cure or
reinstatement. Keep retired binary downloads retired until their independent
source/resource/delivery requirements are resolved. Follow ../BUILDING.md for
the current supported companion and player-supplied original DOS IWAD workflow.
