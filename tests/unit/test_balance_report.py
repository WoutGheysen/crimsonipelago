from crimsonipelago.tools.balance_report import report

SPOILER = """Archipelago

Locations:

Unfamiliar Lands: Enter the cabin: Flight
Unfamiliar Lands: Speak with Marius: 100 Copper
Somewhere Else: Thing (Other): Sword (Other)

Playthrough:
"""


def test_report_counts_crimson_checks_and_watched_items(tmp_path, real_catalogue):
    (tmp_path / "o1").mkdir()
    (tmp_path / "o1" / "AP_1_Spoiler.txt").write_text(SPOILER)
    lines = report(tmp_path, ["Flight"], real_catalogue)
    assert lines[0] == "1 seeds, 2 Crimson Desert checks each"
    assert lines[1].startswith("Flight: check ") and "chapters {1: 1}" in lines[1]
    assert "100 Copper 1.0" in lines[2]
