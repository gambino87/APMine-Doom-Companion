import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


PROBE = r'''
#include <map>
#include <stdio.h>
#include "practice_items.h"
struct item { int doom_type, ep, map; };
int main() {
    std::map<int64_t, item> table = {
        {350003, {13, 1, 2}}, {350100, {2001, -1, -1}},
        {350110, {2022, -1, -1}}, {350119, {-2, 1, 2}},
        {359002, {2001, 1, 2}}, {359906, {2022, -1, -1}},
        {350010, {13, 1, 3}}, {350020, {13, 2, 2}}
    };
    printf("%lld\n", (long long)APMinePracticeItemId(table, 13, 1, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 13, 1, 3));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 13, 2, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 13, 3, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 2001, 1, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 2022, 1, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, -2, 1, 2));
    printf("%lld\n", (long long)APMinePracticeItemId(table, 350003, 1, 2));
}
'''


class DoomPracticeItemTests(unittest.TestCase):
    def test_actual_cpp_lookup_resolves_current_ids_and_exact_scope(self):
        compiler = shutil.which("cl.exe" if os.name == "nt" else "c++")
        if not compiler:
            self.skipTest("A local C++ compiler is required for the native lookup test")
        from tools.doom_companion import bootstrap
        include = Path(bootstrap.__file__).parent
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "probe.cpp"
            executable = root / ("probe.exe" if os.name == "nt" else "probe")
            source.write_text(PROBE)
            arguments = ([compiler, "/nologo", "/EHsc", "/std:c++17", "/I" + str(include), str(source),
                          "/Fe" + str(executable), "/Fo" + str(root / "probe.obj")] if os.name == "nt" else
                         [compiler, "-std=c++11", "-I", str(include), str(source), "-o", str(executable)])
            result = subprocess.run(arguments, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines(),
                             ["350003", "350010", "350020", "0", "350100", "350110", "350119", "0"])


if __name__ == "__main__":
    unittest.main()
