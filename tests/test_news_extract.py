import unittest
from datetime import datetime, timezone

from v2.news_extract import extract_claims


PLAYERS = [
    {"player_id": 12, "club": "ARS", "canonical": "Bukayo Saka", "aliases": ["Saka", "Bukayo Saka"]},
    {"player_id": 13, "club": "ARS", "canonical": "Martin Odegaard", "aliases": ["Odegaard", "Martin Odegaard"]},
]


class NewsExtractionTests(unittest.TestCase):
    NOW = datetime(2026, 8, 21, 15, tzinfo=timezone.utc)

    def extract(self, text, *, published=True, fixture_terms=None):
        document = self.document(text)
        if not published:
            document["published_at"] = None
        return extract_claims(document, PLAYERS, gw=1, now=self.NOW,
                              fixture_terms=fixture_terms or {"fulham"})

    def document(self, text):
        return {
            "source_id": "ars-team-news", "publisher": "Arsenal", "club": "ARS",
            "url": "https://www.arsenal.com/news/team-news", "title": "Team news",
            "text": text, "published_at": "2026-08-21T12:00:00Z",
        }

    def test_explicit_absence_is_safe_to_apply(self):
        claims = self.extract("Bukayo Saka will miss the match against Fulham through injury.")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_type"], "explicit_out")
        self.assertEqual(claims[0]["decision"], "applied")
        self.assertEqual(claims[0]["player_id"], 12)

    def test_late_test_remains_a_candidate(self):
        claims = self.extract("Saka will have a late fitness test.")
        self.assertEqual(claims[0]["claim_type"], "late_test")
        self.assertEqual(claims[0]["decision"], "candidate")

    def test_not_ruled_out_is_not_misread_as_absent(self):
        claims = self.extract("Saka has not been ruled out and will be assessed.")
        self.assertTrue(claims)
        self.assertNotIn("applied", {claim["decision"] for claim in claims})

    def test_two_named_players_in_one_sentence_stays_ambiguous(self):
        claims = self.extract("Saka and Odegaard will miss the match against Fulham.")
        self.assertEqual({c["decision"] for c in claims}, {"candidate"})
        self.assertEqual({c["reason"] for c in claims}, {"multiple_players_in_sentence"})

    def test_excerpt_is_bounded(self):
        claims = self.extract("Saka will miss the match against Fulham. " + "x" * 500)
        self.assertLessEqual(len(claims[0]["excerpt"]), 280)

    def test_undated_article_can_never_auto_apply(self):
        claims = self.extract("Saka will miss the match against Fulham.", published=False)
        self.assertEqual(claims[0]["decision"], "candidate")
        self.assertEqual(claims[0]["reason"], "missing_or_stale_publication_time")

    def test_not_fit_to_face_is_an_explicit_absence(self):
        claims = self.extract("Bukayo Saka is not fit to face Fulham.")
        self.assertEqual(claims[0]["decision"], "applied")

    def test_explicit_return_date_is_parsed_for_expiry(self):
        claims = self.extract("Bukayo Saka is expected to be back 6 September.")
        self.assertEqual(claims[0]["claim_type"], "return_date")
        self.assertEqual(claims[0]["decision"], "applied")
        self.assertEqual(claims[0]["return_date"], "2026-09-06")

    def test_explicit_out_for_unmatched_fixture_is_review_only(self):
        claims = self.extract("Saka will miss the match against Chelsea.", fixture_terms={"fulham"})
        self.assertEqual(claims[0]["decision"], "candidate")
        self.assertEqual(claims[0]["reason"], "fixture_not_matched")

    def test_same_opponent_wrong_match_date_is_review_only(self):
        claims = extract_claims(
            self.document("Saka will miss Tuesday's cup match against Fulham."), PLAYERS,
            gw=1, now=self.NOW, fixture_terms={"fulham"},
            fixture_markers={"friday", "21 august", "premier league"})
        self.assertEqual(claims[0]["decision"], "candidate")
        self.assertEqual(claims[0]["reason"], "fixture_not_matched")

    def test_return_date_resolves_within_same_calendar_year_after_new_year(self):
        now = datetime(2027, 2, 20, 12, tzinfo=timezone.utc)
        claims = extract_claims(self.document("Saka is expected to be back 15 March."), PLAYERS,
                                gw=25, now=now, fixture_terms={"fulham"})
        self.assertEqual(claims[0]["return_date"], "2027-03-15")


if __name__ == "__main__":
    unittest.main()
