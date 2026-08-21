import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _weekly():
    return json.loads((ROOT / "data" / "weekly.json").read_text())


def _player_ids_by_name():
    data = json.loads((ROOT / "app" / "src" / "data" / "fpl.json").read_text())
    return {player["name"]: player["id"] for player in data["players"]}


class WeeklyCoherenceTests(unittest.TestCase):
    def test_lineup_and_bench_are_one_complete_squad(self):
        squad = _weekly()["squad"]
        lineup = squad["lineup"]

        self.assertEqual(len(lineup["xi"]), 11)
        self.assertEqual(len(lineup["bench"]), 4)
        self.assertEqual(set(lineup["xi"]), set(squad["ids"]) - set(lineup["bench"]))
        self.assertFalse(set(lineup["xi"]) & set(lineup["bench"]))

    def test_weekly_snapshot_records_when_the_private_squad_was_confirmed(self):
        squad = _weekly()["squad"]

        self.assertNotEqual(squad["source"], "my_squad.txt")
        if squad["source"] == "confirmed pre-deadline squad":
            self.assertTrue(squad["confirmed_at"])
            self.assertTrue(squad["entry_id"])
            self.assertTrue(squad["changes"])

    def test_recorded_sync_changes_are_reflected_in_the_squad_and_lineup(self):
        squad = _weekly()["squad"]
        ids = set(squad["ids"])
        names = _player_ids_by_name()

        for change in squad.get("changes", []):
            if " → " in change:
                outgoing, incoming = change.split(" → ", 1)
                self.assertNotIn(names[outgoing], ids)
                self.assertIn(names[incoming], ids)
            if ";" in change and " starts" in change:
                starter, substitute = change.split(";", 1)
                starter = starter.removesuffix(" starts")
                substitute = substitute.strip().split(" is ", 1)[0]
                self.assertIn(names[starter], squad["lineup"]["xi"])
                self.assertIn(names[substitute], squad["lineup"]["bench"])

    def test_web_bundle_contains_the_current_weekly_snapshot(self):
        weekly = _weekly()
        weekly.pop("digest_md", None)
        app_data = json.loads(
            (ROOT / "app" / "src" / "data" / "fpl.json").read_text()
        )

        self.assertEqual(app_data["weekly"], weekly)


if __name__ == "__main__":
    unittest.main()
