import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.doom_companion import network_gameplay


class DoomCompanionNetworkGameplayTests(unittest.TestCase):
    def catalog(self):
        return {"item_table": {
            "1": ["Medikit", 2012, -1, -1], "2": ["fixture map", -1, 1, 2],
            "3": ["fixture map red key", 13, 1, 2], "4": ["other map red key", 13, 1, 3],
        }, "location_table": {"1": {"2": {"0": 11, "1": 12, "-1": 13}}},
            "level_info": [[{}, {"thing_list": [[2012, 11], [13, 12]]}]]}

    def test_fixture_key_placement_uses_its_map_and_preserves_completion_check(self):
        items, placements = network_gameplay.fixture_catalog(self.catalog())
        self.assertEqual(2, items["fixture_map_access"])
        self.assertEqual({11: 1, 12: 3, 13: 1}, placements)

    def test_missing_map_access_or_matching_key_is_rejected(self):
        for missing, message in (("2", "access item"), ("3", "matching per-map")):
            with self.subTest(missing=missing):
                catalog = self.catalog()
                del catalog["item_table"][missing]
                with self.assertRaisesRegex(ValueError, message):
                    network_gameplay.fixture_catalog(catalog)

    def test_optional_un_generated_checks_are_not_injected_into_server(self):
        catalog = self.catalog()
        catalog["location_table"]["1"]["2"]["-2"] = 14
        _, placements = network_gameplay.fixture_placements(catalog, {location: (1, 1, 0) for location in (11, 12, 13)})
        self.assertEqual({11: 1, 12: 3, 13: 1}, placements)

    def test_required_generated_pickup_key_and_completion_checks_cannot_be_omitted(self):
        for missing in (11, 12, 13):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "pickup/key/completion"):
                network_gameplay.fixture_placements(self.catalog(), {location: (1, 1, 0)
                    for location in (11, 12, 13) if location != missing})

    def test_existing_or_overlapping_private_output_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaises(FileExistsError):
                network_gameplay.require_private_output(root, ())
            with self.assertRaisesRegex(ValueError, "separate"):
                network_gameplay.require_private_output(root / "new", (root,))

    def test_non_windows_or_non_private_output_is_rejected(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, LOCALAPPDATA=temporary):
            output = Path(temporary).resolve() / "outside-private-root"
            with self.assertRaisesRegex(ValueError, "LOCALAPPDATA/APMine"):
                network_gameplay.require_private_output(output, ())

    def test_native_harness_requires_loopback_owned_markers_and_real_server_confirmation(self):
        header = Path(network_gameplay.__file__).with_name("apmine_network_test.h").read_text()
        for token in ('"127.0.0.1:"', '"dummy"', "ap_practice_mode || ap_force_disable_behaviors",
                      "!nomonsters", "APMine-private-network.marker", "APMine-server-checks-confirmed.marker",
                      "P_TouchSpecialThing", "G_LevelSelect", "G_ExitLevel", "state->completed"):
            if token == "P_TouchSpecialThing":
                self.assertIn("APMineTouchPickup", header)
            else:
                self.assertIn(token, header)
        self.assertIn("ap_state.player_state.max_ammo[am_clip]", header)
        self.assertIn("player->maxammo[am_clip]", header)
        self.assertIn("player->weaponowned[wp_chainsaw]", header)
        self.assertIn("++map_frames < 8", header)

    def test_expected_inventory_is_not_read_back_from_native_results(self):
        self.assertEqual(203, network_gameplay.EXPECTED["max_health"])
        self.assertEqual([220, 50, 300, 50], network_gameplay.EXPECTED["max_ammo"])
        self.assertEqual(2, network_gameplay.INITIAL_ITEMS.count("Progressive weapon"))
        self.assertEqual(3, network_gameplay.INITIAL_ITEMS.count("Bullet Capacity Upgrade"))


class DoomCompanionNetworkProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_reaps_only_owned_child_and_redirects_saves(self):
        server = ModuleType("MultiServer")
        server.get_received_items = lambda *args: []
        child = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=-9))
        child.kill = Mock(side_effect=lambda: setattr(child, "returncode", -9))
        clock = SimpleNamespace(time=Mock(side_effect=[0, 66]))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = SimpleNamespace(location_checks={(0, 1): set()})
            definitions = {"location_table": {"1": {"2": {"-1": 13}}}}
            with patch.dict(sys.modules, MultiServer=server), patch.object(network_gameplay.asyncio,
                    "create_subprocess_exec", new=AsyncMock(return_value=child)) as spawn, patch.object(
                    network_gameplay.asyncio, "get_running_loop", return_value=clock):
                with self.assertRaisesRegex(TimeoutError, "Owned native network case"):
                    await network_gameplay.run_native(root / "owned.exe", root, root / "player.wad", root,
                        "doom", "Fixture", 52183, "initial", context, 1, definitions)
            child.kill.assert_called_once_with()
            child.wait.assert_awaited_once_with()
            arguments = spawn.call_args.args
            self.assertEqual(str(root / "saves"), arguments[arguments.index("-apsavedir") + 1])
            self.assertEqual("127.0.0.1:52183", arguments[arguments.index("-apserver") + 1])
            self.assertEqual(root, spawn.call_args.kwargs["cwd"])


if __name__ == "__main__":
    unittest.main()
