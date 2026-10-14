import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from tools.doom_companion import bootstrap
from tools.doom_companion.inventory import sha256
from tools.doom_companion.local_assets import encode_patch, encode_wad, NATIVE_PICKUP_PROFILE, NATIVE_PICKUP_MARKER
from tools.doom_companion.inventory import wad_lumps


def fixture_iwad(game):
    lumps = {"PLAYPAL": bytes(value for color in range(256) for value in (color, color, color)),
             "INTERPIC": encode_patch([[32] * 8 for _ in range(8)]),
             "STBAR": encode_patch([[64] * 8 for _ in range(8)])}
    lumps.update({"MAP01": b"", "MAP32": b""} if game == "doom2" else {"E1M1": b"", "E4M1": b""})
    return b"IWAD" + encode_wad(lumps)[4:]


def fixture_rules(game, version="test", extra=None):
    module, title, iwad = bootstrap.GAMES[game]
    manifest = {"game": title, "world_version": version, "__apdoom": {
        "short_name": game, "iwad": iwad, "definitions": f"{module}/apdoom_definitions.json"}}
    members = {name: b"# synthetic rules fixture\n" for name in bootstrap.PYTHON_MEMBERS}
    members.update({"archipelago.json": json.dumps(manifest).encode(), "apdoom_definitions.json": b"{}",
                    "LICENSE.txt": b"synthetic license notice", "THIRD_PARTY_NOTICES.txt": b"synthetic attribution",
                    "docs/setup_en.md": b"setup fixture", f"docs/en_{title}.md": b"description fixture"})
    members.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, raw in members.items():
            archive.writestr(f"{module}/{name}", raw)
    return output.getvalue()


class DoomBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = self.root / "client"
        self.client.mkdir()
        self.libraries = self.root / "libraries"
        self.libraries.mkdir()
        records = []
        for name in sorted(bootstrap.CORE_BINARIES | bootstrap.RUNTIME_LIBRARIES):
            path = (self.client if name in bootstrap.CORE_BINARIES else self.libraries) / name
            path.write_bytes(b"MZ synthetic native fixture " + name.encode())
            records.append({"name": name, "sha256": sha256(path.read_bytes())})
        self.link = self.root / "source-link.json"
        self.link.write_text(json.dumps({"schema_version": 1, "status": "private-binary-source-link",
                                        "source_snapshot_sha256": "a" * 64, "binaries": records,
                                        "system_library_candidates": ["kernel32.dll"]}))
        self.iwad = self.root / "DOOM2.WAD"
        self.iwad.write_bytes(fixture_iwad("doom2"))
        self.rules = self.root / "doom_ii.apworld"
        self.rules.write_bytes(fixture_rules("doom2"))
        self.state = self.root / "player-state"

    def prepare(self, **overrides):
        options = dict(client=self.client, libraries=[self.libraries], source_link=self.link,
                       iwads=[self.iwad], rules=[self.rules], state_root=self.state)
        options.update(overrides)
        return bootstrap.prepare_runtime(**options)

    def test_first_run_constructs_resources_and_never_changes_originals_or_binaries(self):
        originals = {path: path.read_bytes() for path in (self.iwad, self.rules, *self.client.iterdir(), *self.libraries.iterdir())}
        runtime = self.prepare()
        self.assertTrue((runtime.directory / "BaseAssets.zip").is_file())
        self.assertEqual((runtime.directory / "games/doom_ii.apworld").read_bytes(), originals[self.rules])
        marker = json.loads((runtime.directory / bootstrap.MARKER).read_text())
        self.assertTrue(marker["private_cache"])
        self.assertFalse(marker["release_ready"])
        self.assertEqual(set(marker["managed"]), {"BaseAssets.zip", "games/doom_ii.apworld"})
        self.assertFalse(list(runtime.directory.glob("*.exe")))
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_declared_diagnostics_cannot_enter_normal_player_runtime(self):
        record = json.loads(self.link.read_text())
        for value in (True, 1, "false", None):
            with self.subTest(value=value):
                record["gameplay_diagnostics"] = value
                self.link.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, "without gameplay diagnostics"):
                    self.prepare()
                self.assertFalse(self.state.exists())

    def test_native_pickup_placeholders_require_matching_binary_capability_and_recipe(self):
        record = json.loads(self.link.read_text())
        record.update(pickup_sprite_profile=NATIVE_PICKUP_PROFILE,
                      asset_recipe_sha256=sha256(Path(bootstrap.local_assets.__file__).read_bytes()))
        self.link.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "matching renderer"):
            self.prepare()
        self.assertFalse(self.state.exists())
        binary = self.client / "archipelago-doom.exe"
        binary.write_bytes(binary.read_bytes() + NATIVE_PICKUP_MARKER)
        for entry in record["binaries"]:
            if entry["name"] == binary.name:
                entry["sha256"] = sha256(binary.read_bytes())
        self.link.write_text(json.dumps(record))
        runtime = self.prepare()
        with zipfile.ZipFile(runtime.directory / "BaseAssets.zip") as archive:
            resources = dict(wad_lumps(archive.read("ArchipelagoDoom.wad")))
            self.assertEqual(([[None]], 0, 0), bootstrap.local_assets.decode_patch(resources["APCLA0"]))
        record["asset_recipe_sha256"] = "0" * 64
        self.link.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "matching renderer"):
            self.prepare()

    def test_hash_matched_diagnostic_binary_is_rejected_without_a_diagnostic_flag(self):
        binary = self.client / "archipelago-doom.exe"
        original = binary.read_bytes()
        for marker in bootstrap.DIAGNOSTIC_MARKERS:
            with self.subTest(marker=marker):
                binary.write_bytes(original + marker)
                record = json.loads(self.link.read_text())
                for entry in record["binaries"]:
                    if entry["name"] == binary.name:
                        entry["sha256"] = sha256(binary.read_bytes())
                self.link.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, "diagnostic binary"):
                    self.prepare()
                self.assertFalse(self.state.exists())

    def test_rules_update_retains_saves_and_preserves_previous_managed_version(self):
        runtime = self.prepare()
        saves = runtime.directory / "save/seed-slot/state.json"
        saves.parent.mkdir(parents=True)
        saves.write_text("player progress")
        old = self.rules.read_bytes()
        self.rules.write_bytes(fixture_rules("doom2", version="updated"))
        updated = self.prepare()
        self.assertEqual(updated.directory, runtime.directory)
        self.assertEqual(saves.read_text(), "player progress")
        self.assertEqual((runtime.directory / f"history/{sha256(old)}/games/doom_ii.apworld").read_bytes(), old)
        self.assertEqual(self.prepare(), updated)

    def test_modified_or_unowned_runtime_files_are_never_overwritten(self):
        runtime = self.prepare()
        target = runtime.directory / "games/doom_ii.apworld"
        target.write_bytes(b"user changes")
        with self.assertRaisesRegex(ValueError, "modified"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"user changes")
        (runtime.directory / bootstrap.MARKER).unlink()
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.prepare()

    def test_source_link_mismatches_and_embedded_archives_block_launch_preparation(self):
        executable = self.client / "archipelago-doom.exe"
        executable.write_bytes(b"MZ changed")
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            self.prepare()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("original-art.wad", b"synthetic payload")
        executable.write_bytes(b"MZ" + archive.getvalue())
        record = json.loads(self.link.read_text())
        entry = next(entry for entry in record["binaries"] if entry["name"] == executable.name)
        entry["sha256"] = sha256(executable.read_bytes())
        self.link.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "embedded archive"):
            self.prepare()
        self.assertFalse(self.state.exists())

    def test_adding_and_removing_a_game_retains_progress_and_only_manages_selected_rules(self):
        runtime = self.prepare()
        save = runtime.directory / "save/player-state.json"
        save.parent.mkdir()
        save.write_text("existing progress")
        iwad = self.root / "DOOM.WAD"
        iwad.write_bytes(fixture_iwad("doom"))
        rules = self.root / "doom_1993.apworld"
        rules.write_bytes(fixture_rules("doom"))
        both = self.prepare(iwads=[self.iwad, iwad], rules=[self.rules, rules])
        self.assertEqual(both.directory, runtime.directory)
        self.assertEqual(set(both.iwads), {"doom", "doom2"})
        self.assertEqual(save.read_text(), "existing progress")
        one = self.prepare()
        self.assertEqual(one.directory, runtime.directory)
        self.assertFalse((runtime.directory / "games/doom_1993.apworld").exists())
        self.assertEqual((runtime.directory / f"history/{sha256(rules.read_bytes())}/games/doom_1993.apworld").read_bytes(), rules.read_bytes())
        self.assertEqual(save.read_text(), "existing progress")

    def test_unselected_modified_rules_are_not_deleted(self):
        iwad = self.root / "DOOM.WAD"
        iwad.write_bytes(fixture_iwad("doom"))
        rules = self.root / "doom_1993.apworld"
        rules.write_bytes(fixture_rules("doom"))
        runtime = self.prepare(iwads=[self.iwad, iwad], rules=[self.rules, rules])
        removed = runtime.directory / "games/doom_1993.apworld"
        removed.write_bytes(b"user changes")
        with self.assertRaisesRegex(ValueError, "not removed"):
            self.prepare()
        self.assertEqual(removed.read_bytes(), b"user changes")

    def test_missing_or_ambiguous_runtime_dependencies_are_not_silently_substituted(self):
        library = self.libraries / "sdl2.dll"
        (self.client / library.name).write_bytes(library.read_bytes())
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.prepare()
        (self.client / library.name).unlink()
        library.unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            self.prepare()

    def test_extra_native_binaries_and_unowned_runtime_worlds_are_not_loaded(self):
        foreign = self.client / "unrecorded.dll"
        foreign.write_bytes(b"MZ foreign fixture")
        with self.assertRaisesRegex(ValueError, "Unrecorded native"):
            self.prepare()
        foreign.unlink()
        system = self.libraries / "kernel32.dll"
        system.write_bytes(b"MZ system-shadow fixture")
        with self.assertRaisesRegex(ValueError, "system/CRT"):
            self.prepare()
        system.unlink()
        unused = self.libraries / "unused-codec.dll"
        unused.write_bytes(b"MZ unused fixture")
        runtime = self.prepare()
        self.assertFalse((runtime.directory / unused.name).exists())
        shadow = runtime.directory / "sdl2.dll"
        shadow.write_bytes(b"MZ shadow fixture")
        with self.assertRaisesRegex(ValueError, "shadow"):
            self.prepare()
        self.assertEqual(shadow.read_bytes(), b"MZ shadow fixture")
        shadow.unlink()
        extra = runtime.directory / "games/unreviewed.apworld"
        extra.write_bytes(b"unreviewed fixture")
        with self.assertRaisesRegex(ValueError, "unowned APWorld"):
            self.prepare()
        self.assertEqual(extra.read_bytes(), b"unreviewed fixture")

    def test_rules_with_artwork_native_payloads_empty_notices_or_invalid_identity_are_rejected(self):
        for extra in ({"art.png": b"pixels"}, {"client.exe": b"MZ"}, {"LICENSE.txt": b""}, {"apdoom_definitions.json": b"[]"}):
            self.rules.write_bytes(fixture_rules("doom2", extra=extra))
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                bootstrap.read_rules(self.rules)
        wrong = {"game": "DOOM 1993", "__apdoom": {"short_name": "doom2", "iwad": "DOOM2.WAD", "definitions": "doom_ii/apdoom_definitions.json"}}
        self.rules.write_bytes(fixture_rules("doom2", extra={"archipelago.json": json.dumps(wrong).encode()}))
        with self.assertRaisesRegex(ValueError, "identity"):
            bootstrap.read_rules(self.rules)

    def test_wrong_and_duplicate_game_inputs_do_not_create_a_runtime_profile(self):
        with self.assertRaisesRegex(ValueError, "one rules"):
            self.prepare(rules=[self.rules, self.rules])
        other = self.root / "DOOM.WAD"
        other.write_bytes(fixture_iwad("doom"))
        with self.assertRaisesRegex(ValueError, "matching"):
            self.prepare(iwads=[other])
        self.assertFalse((self.state / "runtime").exists())

    def test_runtime_cannot_be_written_into_source_or_executable_inputs(self):
        for root in (self.client / "data", self.libraries / "data", Path(bootstrap.__file__).resolve().parents[2] / "build/bootstrap-test"):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "outside"):
                self.prepare(state_root=root)
        project = self.root / "other-project"
        (project / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "source/build"):
            self.prepare(state_root=project / "player-cache")
        self.assertFalse((project / "player-cache").exists())

    def test_expansion_bound_is_checked_before_reading_the_game_manifest(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doom_ii/archipelago.json", b" " * (bootstrap.MAX_RULE_BYTES + 1))
        self.rules.write_bytes(output.getvalue())
        with patch.object(bootstrap.zipfile.ZipFile, "read", side_effect=AssertionError("must not decompress")):
            with self.assertRaisesRegex(ValueError, "Expanded"):
                bootstrap.read_rules(self.rules)

    def test_managed_paths_reject_escape_and_symlinks(self):
        for name in ("../outside", "/outside", "safe/../outside", "C:/outside"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                bootstrap.managed_path(self.state, name)
        if os.name != "nt":
            self.state.mkdir()
            (self.state / "runtime").symlink_to(self.client, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse(list(self.client.glob("*/BaseAssets.zip")))

    def test_launch_uses_existing_native_gui_and_only_selected_iwad_search_paths(self):
        runtime = self.prepare()
        with patch.object(bootstrap.subprocess, "Popen") as popen:
            bootstrap.launch(runtime)
        arguments, options = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(arguments, [str(runtime.client)])
        self.assertEqual(options["cwd"], runtime.directory)
        self.assertEqual(options["env"]["DOOMWADPATH"], str(self.iwad))
        self.assertFalse(options.get("shell", False))

    def test_direct_practice_and_network_parameters_are_not_shell_commands(self):
        runtime = self.prepare()
        with patch.object(bootstrap.subprocess, "Popen") as popen:
            bootstrap.launch(runtime, "doom2", server="localhost:52183", player="Player with spaces")
        arguments = popen.call_args.args[0]
        self.assertEqual(Path(arguments[0]).name, "archipelago-doom.exe")
        self.assertEqual(arguments[-4:], ["-apserver", "localhost:52183", "-applayer", "Player with spaces"])
        with patch.object(bootstrap.subprocess, "Popen") as popen:
            bootstrap.launch(runtime, "doom2", practice=True, smoke_test=True)
        self.assertIn("-apmine-smoke-test", popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs["env"]["SDL_VIDEODRIVER"], "dummy")

    def test_finite_launcher_check_uses_the_gui_without_game_or_network_arguments(self):
        runtime = self.prepare()
        with patch.object(bootstrap.subprocess, "Popen") as popen:
            bootstrap.launch(runtime, smoke_test=True)
        self.assertEqual(popen.call_args.args[0], [str(runtime.client), "-apmine-smoke-test", "-nosound"])
        self.assertEqual(popen.call_args.kwargs["env"]["SDL_VIDEODRIVER"], "dummy")

    def test_launcher_scenarios_use_gui_input_and_are_restricted_to_finite_offline_checks(self):
        runtime = self.prepare()
        with patch.object(bootstrap.subprocess, "Popen") as popen:
            bootstrap.launch(runtime, smoke_test=True, launcher_smoke_game="doom2", launcher_smoke_launch=True)
        self.assertEqual(popen.call_args.args[0], [str(runtime.client), "-apmine-smoke-test", "-nosound",
                                                  "-apmine-smoke-game", "doom2", "-apmine-smoke-launch"])
        for options in ({"launcher_smoke_game": "doom2"}, {"smoke_test": True, "launcher_smoke_launch": True},
                        {"smoke_test": True, "launcher_smoke_game": "doom"},
                        {"smoke_test": True, "launcher_smoke_game": "doom2", "game": "doom2", "practice": True},
                        {"smoke_test": True, "launcher_smoke_game": "doom2", "server": "localhost", "player": "Slot"}):
            with self.subTest(options=options), patch.object(bootstrap.subprocess, "Popen") as popen, self.assertRaises(ValueError):
                bootstrap.launch(runtime, **options)
            popen.assert_not_called()

    def test_invalid_launch_combinations_never_start_a_process(self):
        runtime = self.prepare()
        for options in ({"practice": True}, {"game": "doom"}, {"game": "doom2"}, {"game": "doom2", "server": "localhost"},
                        {"game": "doom2", "practice": True, "server": "localhost", "player": "Slot"}, {"game": "doom2", "smoke_test": True}):
            with self.subTest(options=options), patch.object(bootstrap.subprocess, "Popen") as popen, self.assertRaises(ValueError):
                bootstrap.launch(runtime, **options)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
