import io
import unittest
import urllib.error
from unittest.mock import patch

from v2.news_fetch import _request, fetch_source, PARSER_VERSION


SOURCE = {"id": "ars-news", "club": "ARS", "publisher": "Arsenal",
          "url": "https://www.arsenal.com/news", "enabled": True}


class NewsFetchTests(unittest.TestCase):
    def test_conditional_request_treats_304_as_unchanged(self):
        error = urllib.error.HTTPError(SOURCE["url"], 304, "Not Modified", {}, io.BytesIO())
        with patch("v2.news_fetch.urllib.request.urlopen", side_effect=error) as call:
            body, result = _request(SOURCE["url"], conditional={"etag": '"abc"'})
        error.close()
        self.assertIsNone(body)
        self.assertTrue(result["not_modified"])
        self.assertEqual(call.call_args.args[0].headers["If-none-match"], '"abc"')

    def test_source_failure_is_visible_in_health(self):
        with patch("v2.news_fetch._request", side_effect=RuntimeError("down")):
            documents, health = fetch_source(SOURCE)
        self.assertEqual(documents, [])
        self.assertEqual(health["status"], "error")

    def test_index_without_articles_is_not_complete_news_coverage(self):
        with patch('v2.news_fetch._request', return_value=('<title>News</title>', {})):
            _, health = fetch_source(SOURCE)
        self.assertEqual(health['status'], 'no_articles')

    def test_healthy_unchanged_index_still_rechecks_updated_article(self):
        url = 'https://www.arsenal.com/news/team-news'
        prior = {'status': 'ok', 'etag': 'index', 'article_validators': {url: {'etag': 'old'}}}
        with patch('v2.news_fetch._request', side_effect=[
                (None, {'not_modified': True}), ('<title>Now available</title>', {'etag': 'new'})]) as request:
            documents, health = fetch_source(SOURCE, prior=prior)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(documents[0]['title'], 'Now available')
        self.assertEqual(health['article_validators'][url]['etag'], 'new')

    def test_index_outage_preserves_prior_evidence_locations(self):
        url = 'https://www.arsenal.com/news/team-news'
        with patch('v2.news_fetch._request', side_effect=RuntimeError('offline')):
            _, health = fetch_source(SOURCE, prior={'article_validators': {url: {}}})
        self.assertEqual(health['_unchanged_articles'], [url])
        self.assertEqual(health['status'], 'error')

    def test_discovered_articles_use_and_persist_conditional_validators(self):
        index = '<a href="/news/team-news">Team news</a>'
        calls = [
            (index, {"etag": '"index-new"'}),
            (None, {"not_modified": True}),
        ]
        prior = {"parser_version": PARSER_VERSION, "article_validators": {
            "https://www.arsenal.com/news/team-news": {"etag": '"article-old"'}
        }}
        with patch("v2.news_fetch._request", side_effect=calls) as request:
            _, health = fetch_source(SOURCE, prior=prior)
        self.assertEqual(request.call_args_list[1].kwargs["conditional"], {"etag": '"article-old"'})
        self.assertEqual(health["article_validators"], prior["article_validators"])
        self.assertEqual(health["_unchanged_articles"], ["https://www.arsenal.com/news/team-news"])

    def test_failed_article_preserves_prior_claim_url_fail_safe(self):
        index = '<a href="/news/team-news">Team news</a>'
        prior = {"parser_version": PARSER_VERSION, "article_validators": {
            "https://www.arsenal.com/news/team-news": {"etag": '"article-old"'}
        }}
        with patch("v2.news_fetch._request", side_effect=[(index, {}), RuntimeError("timeout")]):
            _, health = fetch_source(SOURCE, prior=prior)
        self.assertIn("https://www.arsenal.com/news/team-news", health["_unchanged_articles"])
        self.assertEqual(health["article_validators"], prior["article_validators"])
        self.assertEqual(health["status"], "error")

    def test_one_failed_article_marks_source_partial(self):
        index = ('<a href="/news/team-news">Team news</a>'
                 '<a href="/news/injury-news">Injury news</a>')
        article = '<title>Team news</title><time datetime="2026-08-21">Today</time>'
        with patch("v2.news_fetch._request", side_effect=[
                (index, {}), (article, {"etag": '"one"'}), RuntimeError("timeout")]):
            _, health = fetch_source(SOURCE)
        self.assertEqual(health["status"], "partial")

    def test_partial_source_retries_articles_when_index_is_304(self):
        article_url = "https://www.arsenal.com/news/team-news"
        prior = {"id": SOURCE["id"], "club": "ARS", "publisher": "Arsenal",
                 "url": SOURCE["url"], "status": "partial", "etag": '"index"',
                 "content_hash": "old", "article_validators": {article_url: {}}}
        article = '<title>Recovered</title><time datetime="2026-08-21">Today</time>'
        with patch("v2.news_fetch._request", side_effect=[
                (None, {"not_modified": True}), (article, {"etag": '"recovered"'})]) as request:
            documents, health = fetch_source(SOURCE, prior=prior)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(documents[0]["url"], article_url)
        self.assertEqual(health["status"], "ok")

    def test_parser_upgrade_forces_new_bytes_and_uses_article_publication_date(self):
        url = 'https://www.arsenal.com/news/team-news'
        prior = {'etag': 'index-old', 'article_validators': {url: {'etag': 'article-old'}}}
        article = ('<time datetime="2026-09-12T15:00:00Z">Kickoff</time>'
                   '<meta property="article:published_time" content="2026-09-11T12:00:00Z">'
                   '<article><p><strong>Saka</strong> will miss the match.</p></article>')
        with patch('v2.news_fetch._request', side_effect=[
                ('<a href="/news/team-news">Team news</a>', {}), (article, {})]) as request:
            docs, health = fetch_source(SOURCE, prior=prior)
        self.assertIsNone(request.call_args_list[0].kwargs['conditional']['etag'])
        self.assertEqual(request.call_args_list[1].kwargs['conditional'], {})
        self.assertEqual(docs[1]['text'], 'Saka will miss the match.')
        self.assertEqual(docs[1]['published_at'], '2026-09-11T12:00:00+00:00')
        self.assertEqual(health['parser_version'], PARSER_VERSION)

    def test_hydrated_news_links_are_collected_but_academy_links_are_not(self):
        index = ('<div data-component="FeaturedArticle" data-props=\'{"url":"/news/team-news"}\'></div>'
                 '<a href="/news/u18-team-news">U18 team news</a>')
        with patch('v2.news_fetch._request', side_effect=[(index, {}), ('<title>News</title>', {})]) as request:
            _, health = fetch_source(SOURCE)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(health['status'], 'ok')


if __name__ == "__main__":
    unittest.main()
