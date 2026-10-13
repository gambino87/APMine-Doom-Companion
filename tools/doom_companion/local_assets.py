"""Build a private runtime resource cache from explicitly supplied Doom IWADs."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import struct
import zipfile

from .inventory import sha256, wad_lumps


PixelRows = list[list[int | None]]


def decode_patch(raw: bytes) -> tuple[PixelRows, int, int]:
    if len(raw) < 8:
        raise ValueError("Truncated Doom patch header")
    width, height, left, top = struct.unpack_from("<hhhh", raw)
    if not 1 <= width <= 1024 or not 1 <= height <= 255 or len(raw) < 8 + 4 * width:
        raise ValueError("Invalid Doom patch dimensions/directory")
    pixels: PixelRows = [[None] * width for _ in range(height)]
    for x in range(width):
        offset = struct.unpack_from("<I", raw, 8 + 4 * x)[0]
        if not 8 + 4 * width <= offset < len(raw):
            raise ValueError("Doom patch column lies outside the pixel data")
        for _ in range(height + 1):
            if offset >= len(raw):
                raise ValueError("Truncated Doom patch column")
            y = raw[offset]
            if y == 255:
                break
            if offset + 4 > len(raw):
                raise ValueError("Truncated Doom patch post")
            length = raw[offset + 1]
            if offset + length + 4 > len(raw) or y + length > height:
                raise ValueError("Doom patch post lies outside the image")
            for delta in range(length):
                pixels[y + delta][x] = raw[offset + 3 + delta]
            offset += length + 4
        else:
            raise ValueError("Doom patch column has no terminator")
    return pixels, left, top


def encode_patch(pixels: PixelRows, left: int = 0, top: int = 0) -> bytes:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    if not 1 <= width <= 1024 or not 1 <= height <= 255 or any(len(row) != width for row in pixels):
        raise ValueError("Invalid Doom patch pixel grid")
    header = struct.pack("<hhhh", width, height, left, top)
    offsets = bytearray()
    body = bytearray()
    for x in range(width):
        offsets.extend(struct.pack("<I", 8 + width * 4 + len(body)))
        y = 0
        while y < height:
            if pixels[y][x] is None:
                y += 1
                continue
            start = y
            column = bytearray()
            while y < height and pixels[y][x] is not None:
                column.append(pixels[y][x])
                y += 1
            body.extend(bytes((start, len(column), 0)) + column + b"\0")
        body.append(255)
    return header + offsets + body


def encode_wad(lumps: dict[str, bytes]) -> bytes:
    body = bytearray()
    directory = bytearray()
    for name, raw in lumps.items():
        if not 1 <= len(name) <= 8:
            raise ValueError(f"Invalid WAD lump name: {name}")
        directory.extend(struct.pack("<ii8s", 12 + len(body), len(raw), name.encode("ascii")))
        body.extend(raw)
    return struct.pack("<4sii", b"PWAD", len(lumps), 12 + len(body)) + body + directory


def resize(pixels: PixelRows, width: int, height: int) -> PixelRows:
    return [[pixels[y * len(pixels) // height][x * len(pixels[0]) // width]
             for x in range(width)] for y in range(height)]


def runtime_resources(wads: dict[str, dict[str, bytes]], sources: list[dict]) -> bytes:
    supplied = {}
    for game in ("doom", "doom2"):
        supplied.update(wads.get(game, {}))
    palette = supplied.get("PLAYPAL", b"")
    if len(palette) < 768 or len(palette) % 768:
        raise ValueError("The selected IWAD lacks a valid PLAYPAL")

    def color(rgb: tuple[int, int, int]) -> int:
        return min(range(256), key=lambda i: sum((palette[i * 3 + c] - rgb[c]) ** 2 for c in range(3)))

    ink = color((230, 230, 230))
    teal = color((72, 196, 188))
    dark = color((20, 24, 28))
    red = color((216, 64, 64))

    def badge(width: int, height: int, selected: int, left: int = 0, top: int = 0) -> bytes:
        pixels = [[None] * width for _ in range(height)]
        for y in range(height):
            for x in range(width):
                if 1 <= x < width - 1 and 1 <= y < height - 1:
                    pixels[y][x] = selected if x in (1, width - 2) or y in (1, height - 2) else dark
        if width >= 8 and height >= 8:
            for y in range(3, height - 3):
                pixels[y][width // 2] = ink
            for x in range(3, width - 3):
                pixels[height // 2][x] = ink
        return encode_patch(pixels, left, top)

    def glyph(character: str, scale: int = 1) -> PixelRows:
        source = supplied.get(f"STCFN{ord(character.upper()):03d}")
        if source:
            pixels, _, _ = decode_patch(source)
            return resize(pixels, max(1, len(pixels[0]) * scale), len(pixels) * scale)
        return [[None] * (4 * scale) for _ in range(8 * scale)]

    def label(text: str, width: int, height: int) -> bytes:
        letters = [glyph(character) for character in text]
        size = sum(len(letter[0]) + 1 for letter in letters)
        pixels = [[None] * max(1, size) for _ in range(max(len(letter) for letter in letters))]
        x = 0
        for letter in letters:
            for y, row in enumerate(letter):
                pixels[y][x:x + len(row)] = row
            x += len(letter[0]) + 1
        return encode_patch(resize(pixels, width, height))

    background = supplied.get("INTERPIC")
    if background is None:
        raise ValueError("The selected IWAD lacks INTERPIC")
    pixels, _, _ = decode_patch(background)
    background = encode_patch(resize(pixels, 320, 200))
    launcher = {"PLAYPAL": palette, "INTERPIC": background}
    for name, text, width in (("LN_DOOM1", "DOOM", 123), ("LN_DOOM2", "DOOM II", 125),
                              ("LN_HTIC", "HERETIC", 142), ("LN_HACX", "HACX", 142)):
        launcher[name] = label(text, width, 48)
    for code in range(33, 127):
        pixels = glyph(chr(code))
        small_width = max(2, min(6, len(pixels[0])))
        launcher[f"F_SML{code:03d}"] = encode_patch(resize(pixels, small_width, 8))
        launcher[f"F_LRG{code:03d}"] = encode_patch(resize(pixels, small_width * 2, 16))

    common = {
        "CREDITS": b"Player-local reconstruction from explicitly supplied IWADs; do not redistribute this cache.\n",
        "M_DOOM": launcher["LN_DOOM1"], "M_DOOM2": launcher["LN_DOOM2"],
        "NOTIFBG": badge(30, 30, teal), "NOTIFSEL": badge(34, 34, ink, 2, 2),
        "CHECKMRK": badge(10, 8, teal), "REDX": badge(9, 7, red, -1, -1),
        "KEYBG": badge(11, 9, dark), "STYSLASH": label("/", 6, 6),
        "WIAVAIL": badge(18, 18, teal, 9, 9), "WISPLAT": badge(28, 22, teal, 15, 11),
        "WILOCK": badge(16, 19, red, 8, 12), "AMAP": badge(6, 6, teal, 1, 1),
    }
    for name, text, width in (("M_PLAY", "PLAY", 55), ("M_GOAL", "GOAL", 70),
                              ("M_ENERGY", "ENERGY LINK", 138), ("M_KILL", "KILL REQUIREMENT", 141)):
        common[name] = label(text, width, 15)
    # Extend the player's stock status bar into the port's widescreen backing area.
    stock, _, _ = decode_patch(supplied["STBAR"])
    extended = [[dark] * 576 for _ in range(32)]
    for y, row in enumerate(resize(stock, 320, 32)):
        extended[y][128:448] = row
    common["STBAR"] = encode_patch(extended)
    for name in ("D2INTER2", "D2INTER3", "TNTEP2", "TNTEP4", "WIMAP3"):
        common[name] = background
    for index, (left, top) in enumerate(((-2, 15), (62, 15), (-2, -1), (61, -1))):
        common[f"WIURH{index}"] = badge(60, 15, teal, left, top)
    for group, count in ((0, 3), (1, 6), (2, 1)):
        for index in range(count):
            common[f"WIA3{group:02d}{index:02d}"] = encode_patch([[None]])
    common["S_START"] = b""
    for prefix, count, left, top in (("APJI", 7, 14, 39), ("APPI", 7, 14, 23), ("LVST", 4, 14, 28)):
        for index in range(count):
            common[f"{prefix}{chr(65 + index)}0"] = badge(28, 28, teal if prefix != "APPI" else red, left, top)
    aliases = {
        "APBH": ("BON1", 4), "APBA": ("BON2", 4), "APAR": ("ARM1", 2), "APMA": ("ARM2", 2),
        "APSC": ("SOUL", 4), "APMS": ("MEGA", 4), "APIN": ("PINV", 4), "APIS": ("PINS", 4),
    }
    for target, (source, count) in aliases.items():
        for index in range(count):
            name = f"{target}{chr(65 + index)}0"
            common[name] = supplied.get(f"{source}{chr(65 + index)}0", badge(24, 24, teal, 12, 24))
    for name, source in {"APBEA0": "PSTRA0", "APCLA0": "CLIPA0", "APAMA0": "AMMOA0", "APRKA0": "ROCKA0",
                         "APBRA0": "BROKA0", "APCEA0": "CELLA0", "APCPA0": "CELPA0", "APSHA0": "SHELA0",
                         "APSBA0": "SBOXA0", "APBPA0": "BPAKA0"}.items():
        common[name] = supplied.get(source, badge(24, 24, teal, 12, 24))
    common["S_END"] = b""
    common["F_START"] = b""
    common["AP_FLAT"] = bytes(dark if (x // 8 + y // 8) % 2 else teal for y in range(64) for x in range(64))
    common["F_END"] = b""
    manifest = {"schema_version": 1, "private_cache": True, "release_ready": False,
                "supported_games": sorted(wads), "sources": sources,
                "recipe_sha256": sha256(Path(__file__).read_bytes())}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def write(name: str, payload: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)

        write("Launcher.wad", encode_wad(launcher))
        write("ArchipelagoDoom.wad", encode_wad(common))
        # The loader's legacy existence check requires this name; no Heretic art
        # or support is implied by the unused, empty container.
        write("ArchipelagoHeretic.wad", encode_wad({}))
        write("APMine-local-assets.json", json.dumps(manifest, indent=2).encode())
    return output.getvalue()


def build_local_cache(iwads: list[Path], cache_root: Path) -> Path:
    repository = Path(__file__).resolve().parents[2]
    if cache_root.resolve().is_relative_to(repository):
        raise ValueError("Player asset caches must stay outside the platform repository")
    for parent in (cache_root.resolve(), *cache_root.resolve().parents):
        if (parent / "prepare-manifest.json").is_file() or ((parent / "CMakeLists.txt").is_file() and (parent / "src/ap_basic.c").is_file()):
            raise ValueError("Player asset caches must stay outside companion/source build inputs")
    wads = {}
    sources = []
    for path in iwads:
        raw = path.read_bytes()
        if raw[:4] != b"IWAD":
            raise ValueError(f"Select an original IWAD, not a mod/PWAD: {path}")
        lumps = dict(wad_lumps(raw))
        if "MAP01" in lumps and "MAP32" in lumps:
            game = "doom2"
        elif "E1M1" in lumps and "E4M1" in lumps:
            game = "doom"
        else:
            raise ValueError(f"Unsupported IWAD: use Ultimate Doom or Doom II's original DOS data: {path}")
        if game in wads:
            raise ValueError(f"Select only one original IWAD per game: {game}")
        wads[game] = lumps
        sources.append({"game": game, "sha256": sha256(raw)})
    if not wads:
        raise ValueError("At least one player-supplied IWAD is required")
    sources.sort(key=lambda entry: entry["game"])
    key = sha256((json.dumps(sources, sort_keys=True) + sha256(Path(__file__).read_bytes())).encode())[:24]
    output = cache_root / key / "BaseAssets.zip"
    payload = runtime_resources(wads, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != payload:
            raise ValueError("Existing player cache differs; use a new private cache directory")
    else:
        with output.open("xb") as stream:
            stream.write(payload)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iwad", type=Path, action="append", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    args = parser.parse_args()
    output = build_local_cache(args.iwad, args.cache_root)
    print(f"Player-local resources: {output}")
    print("Do not package, upload, or redistribute this cache. It is not a client release.")


if __name__ == "__main__":
    main()
