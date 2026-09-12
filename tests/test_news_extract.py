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

    def test_conditional_questions_and_disputed_absences_never_auto_apply(self):
        for text in (
            'If Saka is ruled out against Fulham, he will be replaced.',
            'Saka will miss Fulham if he fails his fitness test.',
            'Is it confirmed that Saka is ruled out against Fulham?',
            'It is not true that Saka is ruled out against Fulham.',
        ):
            with self.subTest(text=text):
                claims = self.extract(text)
                self.assertTrue(claims)
                self.assertTrue(all(c['decision'] == 'candidate' for c in claims))

    def test_future_publication_cannot_override_current_availability(self):
        document = self.document('Saka will miss Fulham.')
        document['published_at'] = '2026-08-22T12:00:00Z'
        claims = extract_claims(document, PLAYERS, gw=1, now=self.NOW, fixture_terms={'fulham'})
        self.assertEqual(claims[0]['decision'], 'candidate')

    def test_invalid_leap_day_stays_review_only_without_crashing_scan(self):
        claims = self.extract('Saka is expected to be back 29 February.')
        self.assertEqual(claims[0]['decision'], 'candidate')
        self.assertNotIn('return_date', claims[0])

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
        self.assertEqual(claims[0]["decision"], "candidate")
        self.assertEqual(claims[0]["return_date"], "2026-09-06")

    def test_suspension_date_without_a_matching_league_fixture_is_review_only(self):
        claims = self.extract('Saka is suspended until 6 September in the Champions League.')
        self.assertEqual(claims[0]['decision'], 'candidate')

    def test_inline_emphasis_cannot_separate_a_player_from_the_absence(self):
        from v2.public_article import parse_article
        doc = parse_article('<meta property="article:published_time" content="2026-08-21T12:00:00Z">'
                            '<article><p><strong>Bukayo Saka</strong> will <em>miss</em> Fulham.</p>'
                            '<p>Odegaard is available.</p></article>', self.document(''))
        claims = extract_claims(doc, PLAYERS, gw=1, now=self.NOW, fixture_terms={'fulham'})
        self.assertEqual(next(c['decision'] for c in claims if c['player_id'] == 12), 'applied')
        self.assertEqual(next(c['claim_type'] for c in claims if c['player_id'] == 13), 'available')

    def test_a_coach_first_name_is_not_attributed_to_the_player(self):
        from v2.news_extract import _mentions
        players = [dict(player_id=155, club='MCI', canonical='Enzo Fernández',
                        aliases=['Enzo', 'Fernández', 'Enzo Fernández']),
                   dict(player_id=387, club='MCI', canonical="Nico O'Reilly", aliases=["O'Reilly"])]
        names = _mentions('City boss Enzo Maresca says Nico O’Reilly is fit and available.', players, 'MCI')
        self.assertEqual([p['player_id'] for p in names], [387])
        self.assertEqual(_mentions('Enzo Fernandez is available.', players, 'MCI')[0]['player_id'], 155)

    def test_mentioning_a_player_is_insufficient_to_assign_someone_elses_absence(self):
        claims = self.extract('Saka spoke about his teammate who will miss Fulham.')
        self.assertEqual(claims[0]['decision'], 'candidate')
        self.assertEqual(claims[0]['reason'], 'player_not_direct_subject')

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
