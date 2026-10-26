"""Generation tests that run inside Archipelago's own test harness.

WorldTestBase already runs the generic suite for every subclass:
all-state reachability, empty-state reachability and a full fill.
"""

from Options import OptionError

from . import CrimsonDesertTestBase

GOAL_CHECK = "Woman in White: Speak with the woman in white"


class TestDefault(CrimsonDesertTestBase):
    def test_slice_is_beatable_without_items(self) -> None:
        # the story is linear and nothing in the first slice needs an item (the jump is survivable)
        self.assertBeatable(True)

    def test_every_check_is_in_sphere_one(self) -> None:
        reachable = {loc.name for loc in self.multiworld.get_reachable_locations(self.multiworld.state, self.player)
                     if loc.address is not None}
        all_checks = {loc.name for loc in self.multiworld.get_locations(self.player) if loc.address is not None}
        self.assertEqual(reachable, all_checks)
        self.assertIn(GOAL_CHECK, all_checks)
        self.assertEqual(len(all_checks), 42)

    def test_pool_is_flight_artifacts_and_filler(self) -> None:
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        self.assertEqual(names.count("Flight"), 1)
        self.assertEqual(names.count("Abyss Artifact"), 3)
        self.assertLessEqual(set(names), {"Flight", "Abyss Artifact", "100 Copper", "Crow Feather"})
        self.assertEqual(len(names), 42)

    def test_slot_data(self) -> None:
        slot_data = self.world.fill_slot_data()
        for key in ("catalogue_fingerprint", "catalogue_status", "world_version", "goal", "slot_data_version"):
            self.assertIn(key, slot_data)
        self.assertEqual(slot_data["goal"], "first_slice")
        self.assertEqual(slot_data["catalogue_status"], "partial")


class TestConservative(CrimsonDesertTestBase):
    options = {"item_pool_mode": "conservative"}

    def test_flight_is_shuffled(self) -> None:
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        self.assertIn("Flight", names)


class TestQuestsOnly(CrimsonDesertTestBase):
    options = {"included_check_categories": ["quest"]}

    def test_no_abyss_puzzle_checks(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        self.assertNotIn("Ethereal Pathway: Obtain the artifact", names)
        self.assertIn("Unfamiliar Lands: Search the surroundings", names)


class TestTooFewChecks(CrimsonDesertTestBase):
    options = {"included_check_categories": ["boss"]}
    run_default_tests = False

    def setUp(self) -> None:  # generation itself must fail with a clear OptionError
        pass

    def test_generation_fails_cleanly(self) -> None:
        with self.assertRaises(OptionError):
            self.world_setup()


class TestDraftGoalNeedsTheOption(CrimsonDesertTestBase):
    options = {"goal": "chapter_5"}
    run_default_tests = False

    def setUp(self) -> None:
        pass

    def test_draft_goal_without_include_draft_slices_fails_cleanly(self) -> None:
        with self.assertRaises(OptionError):
            self.world_setup()


class TestWholeGameDraft(CrimsonDesertTestBase):
    options = {"goal": "journeys_end", "include_draft_slices": True}

    def test_every_chapter_is_in_the_seed(self) -> None:
        checks = {loc.name for loc in self.multiworld.get_locations(self.player) if loc.address is not None}
        self.assertGreater(len(checks), 400)
        self.assertIn(GOAL_CHECK, checks)
        self.assertIn("Woman in White: Chapter Transition", checks)
        self.assertTrue(self.world.fill_slot_data()["include_draft_slices"])

    def test_flight_then_double_jump_gate_the_story(self) -> None:
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        self.assertEqual(names.count("Flight"), 1)
        self.assertEqual(names.count("Double Jump"), 1)
        self.assertGreater(names.count("Abyss Artifact"), 40)
        self.assertEqual(names.count("Vitality of the Abyss"), 4)
        self.assertFalse(self.can_reach_location("Woman in White: Return to Hernand"))
        self.collect_by_name(["Flight"])
        self.assertTrue(self.can_reach_location("Woman in White: Return to Hernand"))
        self.assertTrue(self.can_reach_location("Reunion: Approach the child"))
        self.assertFalse(self.can_reach_location("For Honor: Go to Hernand Square"))
        self.collect_by_name(["Double Jump"])
        self.assertTrue(self.can_reach_location("For Honor: Go to Hernand Square"))

    def test_the_later_story_abilities_gate_chapters_9_and_11(self) -> None:
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        for ability in ("Focused Force Palm", "Back Hang", "Aerial Maneuver", "Call Dragon"):
            self.assertEqual(names.count(ability), 1)
        self.collect_by_name(["Flight", "Double Jump"])
        self.assertTrue(self.can_reach_location("An Unknown Voice: Find the trials"))
        self.assertFalse(self.can_reach_location("Cloister of Enlightenment: Purify the golden statues"))
        self.collect_by_name(["Focused Force Palm"])
        self.assertTrue(self.can_reach_location("Cloister of Enlightenment: Purify the golden statues"))
        self.assertTrue(self.can_reach_location("Mark of the Scar: Face the guardian deity of Urdavah"))
        self.collect_by_name(["Back Hang"])
        self.assertFalse(self.can_reach_location("Shackles of Fate: Remove the spear from the dragon"))
        self.collect_by_name(["Aerial Maneuver"])
        self.assertTrue(self.can_reach_location("Shackles of Fate: Remove the spear from the dragon"))
        self.assertTrue(self.can_reach_location("Whispers in the Wind: Go to the Nest of Valor"))
        self.assertFalse(self.can_reach_location("Flying Fortress Orbian: Destroy the Flying Fortress Orbian"))
        self.collect_by_name(["Call Dragon"])
        self.assertTrue(self.can_reach_location("Flying Fortress Orbian: Destroy the Flying Fortress Orbian"))
        self.assertEqual(self.world.fill_slot_data()["story_items"], sorted(
            ["Flight", "Double Jump", "Focused Force Palm", "Back Hang", "Aerial Maneuver", "Call Dragon"]))


class TestChapterTwoDraftIsSmaller(CrimsonDesertTestBase):
    options = {"goal": "chapter_2", "include_draft_slices": True}

    def test_only_slices_one_and_two(self) -> None:
        self.assertEqual(self.world.fill_slot_data()["story_items"], ["Double Jump", "Flight"])
        checks = [loc for loc in self.multiworld.get_locations(self.player) if loc.address is not None]
        self.assertEqual(len(checks), 92)
        names = [item.name for item in self.multiworld.itempool if item.player == self.player]
        self.assertNotIn("Vitality of the Abyss", names)


class TestOptionalCategories(CrimsonDesertTestBase):
    options = {"goal": "journeys_end", "include_draft_slices": True,
               "included_check_categories": ["quest", "boss", "exploration", "regional", "abyss_ruins"]}

    def test_regional_quests_and_ruins_hold_only_filler(self) -> None:
        from BaseClasses import LocationProgressType

        extras = [loc for loc in self.multiworld.get_locations(self.player)
                  if loc.name.startswith("Abyss Ruin:") or loc.parent_region.name.startswith("Regional quests")]
        self.assertGreater(len(extras), 700)
        self.assertTrue(all(loc.progress_type == LocationProgressType.EXCLUDED for loc in extras))
        self.assertFalse(any(loc.item.advancement or loc.item.useful for loc in extras if loc.item))


class TestOptionalCategoriesAreOffByDefault(CrimsonDesertTestBase):
    options = {"goal": "chapter_2", "include_draft_slices": True}

    def test_no_ruins_or_regional_quests(self) -> None:
        names = {loc.name for loc in self.multiworld.get_locations(self.player)}
        self.assertFalse(any(n.startswith("Abyss Ruin:") for n in names))
        self.assertEqual(len([n for n in names if n]), 93)          # 92 checks and the goal event


def _item(name: str, player: int, progression: bool):
    from BaseClasses import Item, ItemClassification
    return Item(name, ItemClassification.progression if progression else ItemClassification.filler, None, player)


class TestDraftChecksKeepOthersSafe(CrimsonDesertTestBase):
    options = {"goal": "chapter_2", "include_draft_slices": True}

    def test_draft_checks_refuse_other_players_progression_only(self) -> None:
        draft = self.world.get_location("For Honor: Go to Hernand Square")
        confirmed = self.world.get_location(GOAL_CHECK)
        self.assertFalse(draft.item_rule(_item("Someone's Hookshot", self.player + 1, True)))
        self.assertTrue(draft.item_rule(_item("Someone's Rupees", self.player + 1, False)))
        self.assertTrue(draft.item_rule(_item("Double Jump", self.player, True)))
        self.assertTrue(confirmed.item_rule(_item("Someone's Hookshot", self.player + 1, True)))


class TestDraftChecksMayHoldAnythingWhenAsked(CrimsonDesertTestBase):
    options = {"goal": "chapter_2", "include_draft_slices": True, "draft_checks_keep_others_safe": False}

    def test_draft_checks_take_other_players_progression(self) -> None:
        draft = self.world.get_location("For Honor: Go to Hernand Square")
        self.assertTrue(draft.item_rule(_item("Someone's Hookshot", self.player + 1, True)))
