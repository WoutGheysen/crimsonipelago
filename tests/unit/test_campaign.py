"""Campaign (playthrough) identity from save history."""

from crimsonipelago.game_adapter.campaign import MIN_ANCHORS, CampaignRegistry, anchors
from crimsonipelago.game_adapter.nattkh_backend import DeepState


def state(quests=(), knowledge=()):
    s = DeepState()
    for key, t in quests:
        s.quests[key] = {"state": 5, "completed_time": t}
    for key, t in knowledge:
        s.knowledge.add(key)
        s.knowledge_times[key] = t
    return s


OPENING = [(k, 320) for k in range(1, 15)]            # scripted: completed at the same moment in every run


def run(seed: int, n: int):
    """A playthrough's history after n player-driven events."""
    return [(1000 + i, seed * 1_000_003 + i * 7919) for i in range(n)]


def test_scripted_batches_are_not_anchors():
    s = state(quests=OPENING + [(500, 999)], knowledge=[(40002, 1234)])
    assert anchors(s) == {("q", 500, 999), ("k", 40002, 1234)}


def test_too_little_history_waits(tmp_path):
    reg = CampaignRegistry(tmp_path / "c.json")
    found = reg.identify(state(quests=OPENING + run(1, MIN_ANCHORS - 1)), "acct")
    assert found.campaign is None and "enough history" in found.problem
    assert reg.campaigns == {}


def test_same_campaign_across_saves_and_slots(tmp_path):
    reg = CampaignRegistry(tmp_path / "c.json")
    first = reg.identify(state(quests=OPENING + run(1, 6)), "acct").campaign
    later = reg.identify(state(quests=OPENING + run(1, 40), knowledge=[(40002, 77)]), "acct").campaign
    assert first is not None and later.id == first.id
    # a reload that lost one anchor (e.g. an ability re-granted at a new time) still matches
    lost_one = reg.identify(state(quests=OPENING + run(1, 40)[1:]), "acct").campaign
    assert lost_one.id == first.id


def test_other_campaign_gets_its_own_id(tmp_path):
    reg = CampaignRegistry(tmp_path / "c.json")
    a = reg.identify(state(quests=OPENING + run(1, 10)), "acct").campaign
    b = reg.identify(state(quests=OPENING + run(2, 10)), "acct").campaign
    assert a.id != b.id
    assert reg.identify(state(quests=OPENING + run(1, 30)), "acct").campaign.id == a.id
    assert reg.identify(state(quests=OPENING + run(2, 30)), "acct").campaign.id == b.id


def test_older_save_from_before_registration_matches(tmp_path):
    reg = CampaignRegistry(tmp_path / "c.json")
    a = reg.identify(state(quests=OPENING + run(1, 20)), "acct").campaign
    assert reg.identify(state(quests=OPENING + run(1, MIN_ANCHORS)), "acct").campaign.id == a.id


def test_registry_persists_and_accounts_are_separate(tmp_path):
    reg = CampaignRegistry(tmp_path / "c.json")
    a = reg.identify(state(quests=run(1, 10)), "acct").campaign
    reloaded = CampaignRegistry(tmp_path / "c.json")
    assert reloaded.identify(state(quests=run(1, 12)), "acct").campaign.id == a.id
    other = reloaded.identify(state(quests=run(1, 12)), "someone-else").campaign
    assert other.id != a.id and other.id.startswith("someone-else/")
