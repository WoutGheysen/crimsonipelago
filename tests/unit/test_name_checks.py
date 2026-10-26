import json
import shutil

from crimson_desert import catalogue as cat_mod

from crimsonipelago.tools import name_checks
from crimsonipelago.tools.draft_slices import DATA


def test_markup_becomes_the_shown_name():
    assert name_checks.clean("Learn {Staticinfo:Knowledge:Knowledge_Lift#Nature's Grasp} through observation.") == \
        "Learn Nature's Grasp through observation"


def test_renaming_follows_rules_and_keeps_running_seeds(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    before = cat_mod.load(data)
    # a goal that names a check still called "step N" (the save editor has no text for it)
    goal_key, target = next((k, g.requires["location"]) for k, g in before.goals.items()
                            if name_checks.STEP.match(g.requires["location"]))
    key = before.locations[target].detection["mission_key"]
    renames = name_checks.rename({key: "Do the last thing", 10 ** 12: "nobody"}, data)
    after = cat_mod.load(data)                                                    # validates
    assert renames == {target: target.split(": step")[0] + ": Do the last thing"}
    assert after.goals[goal_key].requires["location"] == renames[target]
    for g in before.goals:
        result = after.compare_seed(before.scope_fingerprint(g), g)
        assert result.playable and not result.changed
    assert json.loads((data / "seed_fingerprints.json").read_text())["seeds"]
