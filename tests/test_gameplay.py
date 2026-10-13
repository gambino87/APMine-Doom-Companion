from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools.doom_companion import gameplay


class DoomGameplayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "acceptance"
        self.output.mkdir()
        self.assets = self.root / "player-local.zip"
        self.assets.write_bytes(b"synthetic private resource fixture")
        self.iwads = {game: self.root / f"{game}.wad" for game in gameplay.GAMES}
        self.rules = {record[0]: (self.root / f"{record[0]}.apworld", b"synthetic rules fixture")
                      for record in gameplay.GAMES.values()}
        self.binary = self.root / "owned-client.exe"
        self.libraries = self.root / "libraries"

    def run_case(self, game="doom", case="carry"):
        return gameplay.run_case(self.binary, self.libraries, self.iwads, self.rules,
                                 self.assets, self.output, game, case)

    def completed_process(self, arguments, **kwargs):
        self.arguments = arguments
        self.environment = kwargs["env"]
        self.directory = Path(kwargs["cwd"])
        kwargs["stdout"].write(self.log)
        if self.capture:
            (self.directory / "APGAME00.png").write_bytes(b"synthetic image fixture; not visually inspected")
        process = Mock()
        process.wait.return_value = self.code
        return process

    def successful_log(self):
        return (b"APMINE_GAMEPLAY: native return policy passed mode=0\n"
                b"APMINE_GAMEPLAY: PASS mode=0 game=doom pickup=4 key=41 completion_recorded=1\n")

    def test_finite_child_uses_only_owned_offline_fixture_and_explicit_resources(self):
        self.log, self.capture, self.code = self.successful_log(), True, 0
        with patch.object(gameplay.subprocess, "Popen", side_effect=self.completed_process):
            result = self.run_case()
        self.assertEqual(self.arguments, [str(self.binary), "-game", "doom", "-iwad", str(self.iwads["doom"]),
                                         "-practice", "-nomonsters", "-nosound", "-apmine-gameplay-test", "carry"])
        self.assertEqual(self.environment["SDL_VIDEODRIVER"], "dummy")
        self.assertEqual(self.environment["SDL_AUDIODRIVER"], "dummy")
        self.assertEqual((self.directory / "APMine-private-gameplay.marker").read_bytes(),
                         b"APMine-private-gameplay-v1\n")
        self.assertEqual((self.directory / "BaseAssets.zip").read_bytes(), self.assets.read_bytes())
        self.assertEqual(result["pickup_index"], 4)
        self.assertFalse(result["capture"]["visually_inspected"])

    def test_failure_wrong_mode_game_duplicate_pass_and_missing_capture_are_rejected(self):
        valid = self.successful_log()
        for number, (log, capture, code) in enumerate((
                (valid, True, 1), (valid.replace(b"mode=0 game", b"mode=1 game"), True, 0),
                (valid.replace(b"game=doom ", b"game=doom2 "), True, 0), (valid + valid, True, 0),
                (valid, False, 0), (valid.splitlines()[1] + b"\n", True, 0))):
            with self.subTest(number=number):
                self.output = self.root / f"reject-{number}"
                self.output.mkdir()
                self.log, self.capture, self.code = log, capture, code
                with patch.object(gameplay.subprocess, "Popen", side_effect=self.completed_process):
                    with self.assertRaisesRegex(RuntimeError, "case failed"):
                        self.run_case()
                self.assertTrue((self.output / "doom-carry/stdout.log").is_file())

    def test_timeout_kills_and_reaps_only_the_child_it_started(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("owned-client", 45), 0]
        with patch.object(gameplay.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.run_case()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_interruption_reaps_the_running_owned_child_before_propagating(self):
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        process.poll.return_value = None
        with patch.object(gameplay.subprocess, "Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                self.run_case()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_unknown_cases_and_existing_directory_fail_before_launch(self):
        with patch.object(gameplay.subprocess, "Popen") as start:
            with self.assertRaises(ValueError):
                self.run_case(case="live")
            (self.output / "doom-carry").mkdir()
            with self.assertRaises(FileExistsError):
                self.run_case()
        start.assert_not_called()


class DoomGameplayPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.build = self.root / "build"
        self.prefix = self.root / "libraries"
        self.output = self.root / "acceptance"
        for root, names in ((self.build / "bin", gameplay.CORE_BINARIES),
                            (self.prefix / "bin", gameplay.RUNTIME_LIBRARIES)):
            root.mkdir(parents=True)
            for name in names:
                (root / name).write_bytes(b"synthetic native fixture")
        for path, helper in (("src/doom/apmine_gameplay_test.h", "apmine_gameplay_test.h"),
                             ("src/archipelago/practice_items.h", "practice_items.h")):
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(gameplay.__file__).with_name(helper).read_bytes())
        for name in ("src/doom/d_main.c", "src/doom/CMakeLists.txt", "src/doom/g_game.c",
                     "src/doom/p_inter.c", "src/archipelago/apdoom.cpp"):
            (self.source / name).write_bytes(b"synthetic source fixture")
        self.cache = self.build / "CMakeCache.txt"
        self.cache.write_text(f"CMAKE_HOME_DIRECTORY:INTERNAL={self.source.as_posix()}\n"
                              f"CMAKE_PREFIX_PATH:PATH={self.prefix.as_posix()}\n"
                              "APMINE_GAMEPLAY_DIAGNOSTICS:BOOL=ON\n")
        self.iwads = {game: self.root / f"{game}.wad" for game in gameplay.GAMES}
        for path in self.iwads.values():
            path.write_bytes(b"synthetic original fixture")
        self.rules = [self.root / f"{game}.apworld" for game in gameplay.GAMES]
        for path in self.rules:
            path.write_bytes(b"synthetic rules fixture")
        self.assets = self.root / "private-assets.zip"
        self.assets.write_bytes(b"synthetic private assets")
        self.validate = patch.object(gameplay, "validate_prepared_source")
        self.validate.start()
        self.addCleanup(self.validate.stop)
        self.read = patch.object(gameplay, "read_rules", side_effect=lambda path: (path.stem, path.read_bytes()))
        self.read.start()
        self.addCleanup(self.read.stop)

    def collect(self):
        return gameplay.collect(self.source, self.build, self.prefix, self.iwads, self.rules, self.output)

    def test_six_cases_remain_private_and_do_not_claim_network_or_release_clearance(self):
        with patch.object(gameplay, "build_local_cache", return_value=self.assets), \
                patch.object(gameplay, "run_case", return_value={}) as run:
            result = self.collect()
        self.assertEqual(run.call_count, 6)
        self.assertFalse(result["release_ready"])
        self.assertFalse(result["published"])
        self.assertIn("no live AP server", result["limitations"][0])
        self.assertEqual(len(result["native_inputs"]), 13)

    def test_normal_build_is_rejected_before_creating_output(self):
        self.cache.write_text(self.cache.read_text().replace("BOOL=ON", "BOOL=OFF"))
        with self.assertRaisesRegex(ValueError, "explicitly diagnostic"):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_unmatched_native_headers_are_rejected_before_creating_output(self):
        for name in ("src/doom/apmine_gameplay_test.h", "src/archipelago/practice_items.h"):
            path = self.source / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(b"changed native fixture")
                with self.assertRaisesRegex(ValueError, "differs"):
                    self.collect()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_duplicate_rules_and_missing_originals_fail_before_output(self):
        self.rules = [self.rules[0], self.rules[0]]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.collect()
        self.assertFalse(self.output.exists())
        self.iwads.pop("doom2")
        with self.assertRaisesRegex(ValueError, "explicit original"):
            self.collect()

    def test_existing_or_overlapping_output_is_not_reused(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.collect()
        self.output = self.source / "nested"
        with self.assertRaisesRegex(ValueError, "separate"):
            self.collect()

    def test_mutation_guard_runs_even_when_an_owned_case_fails(self):
        def failing_case(*args):
            self.iwads["doom"].write_bytes(b"changed synthetic original fixture")
            raise RuntimeError("synthetic child failure")
        with patch.object(gameplay, "build_local_cache", return_value=self.assets), \
                patch.object(gameplay, "run_case", side_effect=failing_case):
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                self.collect()


if __name__ == "__main__":
    unittest.main()
