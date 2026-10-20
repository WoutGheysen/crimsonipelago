"""Generation tests that run inside Archipelago's own test harness.

WorldTestBase already runs the generic suite for every subclass:
all-state reachability, empty-state reachability and a full fill.
"""

from Options import OptionError

from . import CrimsonDesertTestBase


class TestDefault(CrimsonDesertTestBase):
    def test_goal_requires_story_access(self) -> None:
        self.assertFalse(self.multiworld.state.has("Victory", self.player))
        self.assertBeatable(False)
        self.collect_by_name(["Story Access: Act 1", "Story Access: Act 2", "Story Access: Act 3"])
        self.assertBeatable(False)  # still needs the equipment for the Act 3 event
        self.collect_by_name(["Equipment Upgrade"])
        self.assertBeatable(True)

    def test_sphere_one_has_checks(self) -> None:
        reachable = [loc for loc in self.multiworld.get_reachable_locations(self.multiworld.state, self.player)
                     if loc.address is not None]
        self.assertGreater(len(reachable), 0)

    def test_vault_needs_key_and_interaction(self) -> None:
        self.assertAccessDependency(["Vault Treasure 1", "Vault Treasure 2"], [["Key Item"]], only_check_listed=True)
        self.assertAccessDependency(["Vault Treasure 1", "Vault Treasure 2"], [["Interaction Unlock"]],
                                    only_check_listed=True)

    def test_slot_data(self) -> None:
        slot_data = self.world.fill_slot_data()
        for key in ("catalogue_fingerprint", "catalogue_status", "world_version", "goal", "slot_data_version"):
            self.assertIn(key, slot_data)


class TestConservative(CrimsonDesertTestBase):
    options = {"item_pool_mode": "conservative"}

    def test_left_out_unlocks_are_precollected(self) -> None:
        precollected = {item.name for item in self.multiworld.precollected_items[self.player]}
        self.assertIn("Traversal Unlock B", precollected)


class TestQuestsAndBossesOnly(CrimsonDesertTestBase):
    options = {"included_check_categories": ["quest", "boss"]}

    def test_no_treasure_locations(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        self.assertNotIn("Vault Treasure 1", names)
        self.assertIn("Prologue Quest 1", names)


class TestTooFewChecks(CrimsonDesertTestBase):
    options = {"included_check_categories": ["boss"]}
    run_default_tests = False

    def setUp(self) -> None:  # generation itself must fail with a clear OptionError
        pass

    def test_generation_fails_cleanly(self) -> None:
        with self.assertRaises(OptionError):
            self.world_setup()
