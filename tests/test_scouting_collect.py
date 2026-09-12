import unittest
from v2.scouting_collect import parse_article, candidate_urls
from v2.scouting import named


class CollectorTests(unittest.TestCase):
    def test_brentford_publisher_header_date(self):
        doc = parse_article('<div data-component="NewsHeader" data-prop-date="2026-09-05T16:19:12.9710000&#x2B;00:00"></div>', {'url':'https://example.com/report'})
        self.assertEqual(doc['published_at'], '2026-09-05T16:19:12.971000+00:00')
        self.assertEqual(doc['date_source'], 'publisher_article_header')

    def test_chelsea_header_and_related_story_are_distinct(self):
        raw = '<div data-component="FeaturedArticle" data-props=\'{"date":"2026-09-05T10:00:00Z"}\'></div>'
        self.assertIsNone(parse_article(raw, {'url':'https://example.com/report'})['published_at'])
        raw += '<div data-component="ArticleHeader" data-props=\'{"articleHeaderDetails":{"date":"2026-09-03T10:00:00.0000000\\\\u002B00:00"}}\'></div>'
        self.assertEqual(parse_article(raw, {'url':'https://example.com/report'})['published_at'], '2026-09-03T10:00:00+00:00')

    def test_hydrated_links_and_first_team_filter(self):
        raw = '''<div data-component="FeaturedArticle" data-props='{"url":"/en/news/article/thiago-match-report"}'></div>
        <a href="/en/news/u18s-report-united">U18 report</a>
        <a href="/en/news/category/match-report">Match report</a>
        <a href="https://other.com/en/news/report">Report</a>
        <a href="/en/news/report-women">Report women</a>'''
        self.assertEqual(candidate_urls(raw, 'https://example.com/news'), ['https://example.com/en/news/article/thiago-match-report'])

    def test_login_content_is_not_scouted(self):
        with self.assertRaisesRegex(ValueError, 'requires_login'):
            parse_article('<div data-component="ArticleLoginOverlay" data-props=\'{"requiresLogin":true}\'></div>', {'url':'https://example.com/report'})

    def test_multiple_jsonld_articles_require_matching_url(self):
        raw = '''<script type="application/ld+json">[{"@type":"NewsArticle","url":"https://example.com/other","datePublished":"2026-09-05T10:00:00Z"},{"@type":"NewsArticle","url":"https://example.com/report","datePublished":"2026-09-04T10:00:00Z"}]</script>'''
        self.assertEqual(parse_article(raw, {'url':'https://example.com/report'})['published_at'], '2026-09-04T10:00:00+00:00')

    def test_player_names_allow_accents_but_not_substrings(self):
        self.assertTrue(named({'name':'João Pedro'}, 'Joao Pedro played.'))
        self.assertFalse(named({'name':'Thiago'}, 'Thiagoa played.'))

    def test_fixture_kickoff_cannot_date_an_article(self):
        doc = parse_article('<time datetime="2026-09-08T19:00:00Z">Kickoff</time>', {'url':'https://example.com/report'})
        self.assertIsNone(doc['published_at'])

    def test_a_single_explicitly_different_jsonld_article_cannot_date_the_page(self):
        raw = '<script type="application/ld+json">{"@type":"NewsArticle","url":"https://example.com/other","datePublished":"2026-09-05T10:00:00Z"}</script>'
        self.assertIsNone(parse_article(raw, {'url': 'https://example.com/report'})['published_at'])

    def test_city_visible_byline_uses_british_summer_time(self):
        doc = parse_article('<p>Sat 05 Sep 2026, 18:15</p>', {'url':'https://www.mancity.com/news/mens/report'})
        self.assertEqual(doc['published_at'], '2026-09-05T17:15:00+00:00')

    def test_nested_related_card_does_not_truncate_the_report(self):
        raw = '<article><p>' + 'Headline '*50 + '</p><article>Related card</article><p>Haaland created a late chance.</p></article>'
        self.assertIn('Haaland created a late chance.', parse_article(raw, {'url':'https://example.com/report'})['text'])
