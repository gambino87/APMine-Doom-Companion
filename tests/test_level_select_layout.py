import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


PROBE = r'''
#include "level_select_layout.h"
int main(void) {
    const int dimensions[][2] = {{63,12},{54,16},{62,16},{54,16},{62,24},{61,36},{200,200},{1,1}};
    int count, i;
    for (i = 0; i < 8; ++i) {
        int num, den, width, height;
        APMineLevelSelectWeaponScale(dimensions[i][0], dimensions[i][1], &num, &den);
        width = (dimensions[i][0] * num + den - 1) / den;
        height = (dimensions[i][1] * num + den - 1) / den;
        if (width > 28 || height > 12 || height < 1 || width < 1) return 1;
        if (APMINE_WEAPON_BOTTOM - height < 186 || APMINE_WEAPON_BOTTOM > 200) return 2;
        for (count = 1; count <= 8; ++count) {
            int n;
            for (n = 0; n < count; ++n) {
                int center = 160 + n * APMINE_WEAPON_SPACING - (count - 1) * APMINE_WEAPON_SPACING / 2;
                if (center - width / 2 < 0 || center + (width + 1) / 2 > 320 || center + 21 > 320) return 3;
            }
        }
    }
    if (APMINE_MAP_KEY_X <= 17) return 4;
    for (count = 10; count <= 11; ++count) {
        int previous = 0, last = 15 + (count - 1) * 17;
        for (i = 0; i < count; ++i) {
            int y = APMineLevelSelectListY(15 + i * 17, 15, last);
            if (y < 26 || y > 172 || (i && y - previous < 14)) return 5;
            previous = y;
        }
    }
    if (APMineLevelSelectListY(15,15,15) != 26) return 6;
    return 0;
}
'''


class DoomLevelSelectLayoutTests(unittest.TestCase):
    def test_compiled_layout_keeps_icons_in_bounds_and_keys_clear_of_text(self):
        compiler = shutil.which("cl.exe" if os.name == "nt" else "cc")
        if not compiler:
            self.skipTest("A C compiler is required for the native layout test")
        from tools.doom_companion import bootstrap
        include = Path(bootstrap.__file__).parent
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "layout.c"
            executable = root / ("layout.exe" if os.name == "nt" else "layout")
            source.write_text(PROBE)
            arguments = ([compiler, "/nologo", "/I" + str(include), str(source),
                          "/Fe" + str(executable), "/Fo" + str(root / "layout.obj")] if os.name == "nt" else
                         [compiler, "-std=c99", "-Wall", "-Werror", "-I", str(include), str(source), "-o", str(executable)])
            result = subprocess.run(arguments, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
