import io
import unittest
import urllib.error
from unittest.mock import patch

from v2.news_fetch import _request, fetch_source


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

    def test_discovered_articles_use_and_persist_conditional_validators(self):
        index = '<a href="/news/team-news">Team news</a>'
        calls = [
            (index, {"etag": '"index-new"'}),
            (None, {"not_modified": True}),
        ]
        prior = {"article_validators": {
            "https://www.arsenal.com/news/team-news": {"etag": '"article-old"'}
        }}
        with patch("v2.news_fetch._request", side_effect=calls) as request:
            _, health = fetch_source(SOURCE, prior=prior)
        self.assertEqual(request.call_args_list[1].kwargs["conditional"], {"etag": '"article-old"'})
        self.assertEqual(health["article_validators"], prior["article_validators"])
        self.assertEqual(health["_unchanged_articles"], ["https://www.arsenal.com/news/team-news"])


if __name__ == "__main__":
    unittest.main()
