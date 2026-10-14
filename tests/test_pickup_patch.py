import ctypes
import os
from pathlib import Path
import shutil
import struct
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tools.doom_companion.local_assets import decode_patch, encode_patch, pickup_symbol


PROBE = r'''
#include "pickup_patch.h"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
EXPORT size_t compose(const unsigned char *base, size_t size, int category,
                      unsigned char *output, size_t capacity) {
    size_t length;
    unsigned char *patch = APMineComposePickupPatch(base, size, category, 200, 32, &length);
    if (!patch) return 0;
    if (length > capacity) { free(patch); return 0; }
    memcpy(output, patch, length);
    free(patch);
    return length;
}
EXPORT int category(unsigned int flags) { return APMinePickupCategoryFromFlags(flags); }
'''


class DoomPickupPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cl.exe" if os.name == "nt" else "cc")
        if not compiler:
            raise unittest.SkipTest("A C compiler is required for the native compositor test")
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        from tools.doom_companion import bootstrap
        include = Path(bootstrap.__file__).parent
        source = root / "probe.c"
        source.write_text(PROBE)
        library = root / ("probe.dll" if os.name == "nt" else "probe.so")
        arguments = ([compiler, "/nologo", "/LD", "/I" + str(include), str(source),
                      "/Fo" + str(root / "probe.obj"), "/link", "/OUT:" + str(library),
                      "/IMPLIB:" + str(root / "probe.lib")] if os.name == "nt" else
                     [compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                      "-I", str(include), str(source), "-o", str(library)])
        result = subprocess.run(arguments, cwd=root, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.library = ctypes.CDLL(str(library))
        cls.library.compose.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                                       ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
        cls.library.compose.restype = ctypes.c_size_t
        cls.library.category.argtypes = [ctypes.c_uint]
        cls.library.category.restype = ctypes.c_int
        if os.name == "nt":
            import _ctypes
            cls.addClassCleanup(_ctypes.FreeLibrary, cls.library._handle)

    def compose(self, base=None, category=0):
        output = (ctypes.c_ubyte * (2 * 1024 * 1024))()
        size = self.library.compose(base, len(base) if base else 0, category, output, len(output))
        return bytes(output[:size])

    def test_categories_use_actual_ap_flags_and_progression_wins_combined_flags(self):
        self.assertEqual([0, 2, 1, 2, 0, 2, 1, 2], [self.library.category(i) for i in range(8)])

    def test_original_symbols_match_the_source_recipe_but_have_distinct_silhouettes(self):
        shapes = []
        for category in range(3):
            actual = self.compose(category=category)
            self.assertEqual(pickup_symbol(category, 200, 32), actual)
            shapes.append(decode_patch(actual)[0])
        self.assertNotEqual(shapes[0], shapes[1])
        self.assertNotEqual(shapes[1], shapes[2])
        self.assertNotEqual(shapes[0], shapes[2])

    def test_composition_preserves_every_base_pixel_and_its_world_anchor(self):
        pixels = [[None, 0], [255, 32], [None, 200]]
        base = encode_patch(pixels, -2, 15)
        original = bytes(base)
        for category in range(3):
            composed, left, top = decode_patch(self.compose(base, category))
            self.assertEqual((11, 11, 2, 23), (len(composed[0]), len(composed), left, top))
            for y, row in enumerate(pixels):
                self.assertEqual(row, composed[y + 8][4:6])
            self.assertEqual(3 - 15, len(composed) - top)
            self.assertTrue(any(value == 200 for row in composed[:8] for value in row))
        self.assertEqual(original, base)

    def test_bounding_sizes_and_negative_origins_round_trip(self):
        for width, height, left, top in ((1, 1, 0, 1), (63, 12, 31, 17), (54, 16, 25, 18),
                                       (61, 36, 31, 38), (1024, 247, -20, -10)):
            with self.subTest(width=width, height=height):
                pixels = [[None if (x + y) % 2 else (x + y) % 256 for x in range(width)] for y in range(height)]
                result, result_left, result_top = decode_patch(self.compose(encode_patch(pixels, left, top)))
                offset = (max(11, width) - width) // 2
                self.assertEqual((max(11, width), height + 8, left + offset, top + 8),
                                 (len(result[0]), len(result), result_left, result_top))
                self.assertEqual(pixels, [row[offset:offset + width] for row in result[8:]])

    def test_malformed_posts_dimensions_origins_and_categories_are_rejected(self):
        valid = encode_patch([[255]], 0, 1)
        invalid = [b"short", valid[:12], valid[:8] + b"\xff" * 4 + valid[12:],
                   encode_patch([[0]] * 248), encode_patch([[0]], 32767, 1),
                   encode_patch([[0]], 0, 32767)]
        broken = bytearray(valid)
        broken[13] = 200
        invalid.append(bytes(broken))
        for base in invalid:
            with self.subTest(base=base[:12]):
                self.assertEqual(b"", self.compose(base))
        for category in (-1, 3, 100):
            self.assertEqual(b"", self.compose(category=category))


if __name__ == "__main__":
    unittest.main()
