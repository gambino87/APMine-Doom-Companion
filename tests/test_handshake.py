import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


PROBE = r'''
#include "ln_handshake.h"
int main(int argc, char **argv) {
    if (argc != 3) return 10;
    if (!strcmp(argv[1], "read")) {
        char result[33] = "unchanged";
        printf("%d:", LN_ReadInitFile(argv[2], result, sizeof(result)));
        puts(result);
        return 0;
    }
    if (!strcmp(argv[1], "collision")) {
#ifdef _WIN32
        unsigned long pid = (unsigned long)_getpid();
#else
        unsigned long pid = (unsigned long)getpid();
#endif
        char old[2048];
        snprintf(old, sizeof(old), "%s%08lx-00000000.tmp", argv[2], pid);
        FILE *file = fopen(old, "wb");
        if (!file) return 11;
        fputs("do-not-delete", file);
        fclose(file);
        puts(old);
    }
    for (int number = 0; number < 10; ++number) {
        char path[2048];
        if (LN_ReserveInitFile(argv[2], path, sizeof(path)) != 0) return 12;
        puts(path);
    }
    return 0;
}
'''


class DoomHandshakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cl.exe" if os.name == "nt" else "cc")
        if not compiler:
            raise unittest.SkipTest("A local C compiler is required for the actual protocol tests")
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.probe = cls.root / ("probe.exe" if os.name == "nt" else "probe")
        source = cls.root / "probe.c"
        source.write_text(PROBE)
        from tools.doom_companion import bootstrap
        include = Path(bootstrap.__file__).parent
        arguments = ([compiler, "/nologo", "/std:c11", "/I" + str(include), str(source),
                      "/Fe" + str(cls.probe), "/Fo" + str(cls.root / "probe.obj")] if os.name == "nt" else
                     [compiler, "-std=c11", "-I", str(include), str(source), "-o", str(cls.probe)])
        result = subprocess.run(arguments, cwd=cls.root, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def setUp(self):
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.directory = Path(self.files.name)

    def run_probe(self, action, path):
        return subprocess.check_output([str(self.probe), action, str(path)], text=True, timeout=10).splitlines()

    def test_missing_empty_and_partial_results_remain_pending_without_caching(self):
        path = self.directory / "result"
        self.assertEqual(self.run_probe("read", path), ["0:unchanged"])
        for raw in (b"", b"O", b"OK", b"ConnectFaile", b"ConnectFailed"):
            path.write_bytes(raw)
            with self.subTest(raw=raw):
                self.assertEqual(self.run_probe("read", path), ["0:unchanged"])

    def test_complete_success_failure_and_crlf_results_are_returned(self):
        path = self.directory / "result"
        for raw, expected in ((b"OK\n", "OK"), (b"OK\r\n", "OK"),
                              (b"ConnectFailed\n", "ConnectFailed"), (b"OldWorldVersion\n", "OldWorldVersion")):
            path.write_bytes(raw)
            with self.subTest(raw=raw):
                self.assertEqual(self.run_probe("read", path), ["1:" + expected])

    def test_malformed_or_oversized_messages_are_not_success_or_cached(self):
        path = self.directory / "result"
        for raw in (b"\n", b"OK\nextra", b"OK\0\n", b"OK \n", b"A" * 34, b"A" * 33 + b"\n"):
            path.write_bytes(raw)
            with self.subTest(raw=raw):
                self.assertEqual(self.run_probe("read", path), ["-1:unchanged"])

    def test_exclusive_reservation_preserves_a_preexisting_path(self):
        paths = self.run_probe("collision", self.directory / ".apdoom-init-")
        self.assertEqual(len(paths), 11)
        self.assertEqual(Path(paths[0]).read_bytes(), b"do-not-delete")
        self.assertEqual(len(set(paths)), 11)
        for name in paths[1:]:
            self.assertEqual(Path(name).read_bytes(), b"")

    def test_distinct_processes_reserve_unique_paths_in_one_shared_directory(self):
        arguments = [str(self.probe), "reserve", str(self.directory / ".apdoom-init-")]
        processes = [subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        def cleanup():
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        self.addCleanup(cleanup)
        paths = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            paths.extend(stdout.splitlines())
        self.assertEqual(len(paths), 20)
        self.assertEqual(len(set(paths)), 20)
        self.assertTrue(all(Path(path).is_file() for path in paths))


if __name__ == "__main__":
    unittest.main()
