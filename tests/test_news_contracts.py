import json
import tempfile
import unittest
from pathlib import Path

from v2.news_contracts import EXPECTED_CLUBS, load_aliases, load_sources


class NewsContractTests(unittest.TestCase):
    def test_source_registry_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "news_sources.json"
            source = {
                "id": "ars-team-news",
                "publisher": "Arsenal",
                "club": "ARS",
                "url": "https://www.arsenal.com/news",
                "source_type": "club_news",
                "parser": "html_article_index",
                "enabled": True,
            }
            path.write_text(json.dumps({"version": 1, "sources": [source, source]}))

            with self.assertRaisesRegex(ValueError, "duplicate source id"):
                load_sources(path)

    def test_project_registry_covers_every_club(self):
        sources = load_sources(Path("v2/news_sources.json"), require_all_clubs=True)
        self.assertEqual({source["club"] for source in sources}, EXPECTED_CLUBS)
        for source in sources:
            self.assertTrue(source["enabled"] or source.get("unsupported_reason"))

    def test_alias_registry_rejects_same_alias_for_two_players_at_one_club(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "player_aliases.json"
            path.write_text(json.dumps({"version": 1, "players": [
                {"player_id": 1, "club": "ARS", "canonical": "One", "aliases": ["Smith"]},
                {"player_id": 2, "club": "ARS", "canonical": "Two", "aliases": ["Smith"]},
            ]}))
            with self.assertRaisesRegex(ValueError, "ambiguous alias"):
                load_aliases(path)


if __name__ == "__main__":
    unittest.main()
