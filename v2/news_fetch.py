"""Bounded public HTTP collector for official club news pages."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


UA = "fpl-2026-team-news/1.0 (+https://github.com/teatimedev/fpl-2026)"
RELEVANT = re.compile(r"team.news|injur|fitness|press|conference|squad|availability|preview", re.I)
EXCLUDE = re.compile(r'\b(?:women|womens|wsl|academy|u18s?|u21s?|under-18|under-21|b-team|tickets?|hospitality)\b', re.I)


try:
    from .public_article import PageParser, ScoutingPage, parse_article
    from .news_extract import EXTRACTION_VERSION
except ImportError:
    from public_article import PageParser, ScoutingPage, parse_article
    from news_extract import EXTRACTION_VERSION

PARSER_VERSION = 'public-article-v2+' + EXTRACTION_VERSION


def _request(url: str, *, timeout: int = 18, attempts: int = 3,
             conditional: dict | None = None) -> tuple[str | None, dict]:
    last = None
    for attempt in range(attempts):
        try:
            headers = {"User-Agent": UA, "Accept": "text/html"}
            if conditional and conditional.get("etag"):
                headers["If-None-Match"] = conditional["etag"]
            if conditional and conditional.get("last_modified"):
                headers["If-Modified-Since"] = conditional["last_modified"]
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.lower():
                    raise ValueError(f"unexpected content type {content_type}")
                raw = response.read(2_500_000)
                return raw.decode(response.headers.get_content_charset() or "utf-8", "replace"), {
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return None, {"not_modified": True}
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.25 * (attempt + 1))
        except (OSError, ValueError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(str(last))


def _same_site(base: str, target: str) -> bool:
    return urllib.parse.urlparse(base).netloc == urllib.parse.urlparse(target).netloc


def fetch_source(source: dict, *, article_limit: int = 5,
                 prior: dict | None = None, relevant=RELEVANT) -> tuple[list[dict], dict]:
    if not source["enabled"]:
        return [], {"id": source["id"], "club": source["club"], "status": "unsupported",
                    "error": source.get("unsupported_reason")}
    try:
        # A 304 validates unchanged bytes, not unchanged extraction rules.
        # Re-fetch once after a parser upgrade before reusing old claims.
        if (prior or {}).get('parser_version') != PARSER_VERSION:
            prior = {**(prior or {}), 'etag': None, 'last_modified': None}
            prior['article_validators'] = {url: {} for url in prior.get('article_validators', {})}
        body, headers = _request(source["url"], conditional=prior)
        prior_articles = (prior or {}).get("article_validators") or {}
        if body is None:
            # An unchanged index says nothing about edits to its articles.
            # Always revalidate the retained article URLs themselves.
            urls = list(prior_articles)[:article_limit]
            documents = []
            headers = {k: (prior or {}).get(k) for k in ("etag", "last_modified")}
        else:
            parser = ScoutingPage(); parser.feed(body)
            urls = []
            for href, label in parser.links:
                absolute = urllib.parse.urljoin(source["url"], href).split("#", 1)[0]
                context = label + ' ' + urllib.parse.urlparse(absolute).path
                if (absolute != source['url'] and _same_site(source["url"], absolute)
                        and relevant.search(context) and not EXCLUDE.search(context)
                        and not re.search(r'/category/|/tag/|/gallery/|/video/', absolute)):
                    if absolute not in urls:
                        urls.append(absolute)
                if len(urls) >= article_limit:
                    break
            documents = [{
                "source_id": source["id"], "publisher": source["publisher"], "club": source["club"],
                "url": source["url"], "title": source["publisher"] + " news index", "text": parser.text,
                "published_at": None,
            }]
        article_errors = []
        article_validators = {}
        unchanged_articles = []
        for url in urls:
            try:
                article, article_headers = _request(url, conditional=prior_articles.get(url))
                if article is None:
                    unchanged_articles.append(url)
                    article_validators[url] = prior_articles.get(url, {})
                    continue
                article_validators[url] = {k: v for k, v in article_headers.items() if v}
                document = parse_article(article, {
                    "source_id": source["id"], "publisher": source["publisher"], "club": source["club"],
                    "url": url,
                })
                document['title'] = (document['title'] or url)[:160]
                if not EXCLUDE.search(document['title']):
                    documents.append(document)
            except (RuntimeError, ValueError) as exc:
                article_errors.append(str(exc))
                # A transient article failure must not erase a previously
                # verified absence from the model. Preserve its claim and its
                # validators until the article can be checked successfully.
                unchanged_articles.append(url)
                article_validators[url] = prior_articles.get(url, {})
        digest = (prior.get("content_hash") if body is None and prior else None) or hashlib.sha256(
            "\n".join(d["text"] for d in documents).encode()).hexdigest()
        loaded_articles = len([doc for doc in documents if doc["url"] != source["url"]])
        status = ("error" if urls and loaded_articles == 0 and article_errors else
                  "partial" if article_errors else "ok" if urls else "no_articles")
        row = {
            "id": source["id"], "club": source["club"], "publisher": source["publisher"],
            "parser_version": PARSER_VERSION,
            "url": source["url"], "status": status, "documents": len(documents),
            "article_errors": len(article_errors), "content_hash": digest,
            "article_validators": article_validators,
            **{k: v for k, v in headers.items() if v},
        }
        if unchanged_articles:
            row["_unchanged_articles"] = unchanged_articles
        if status == "error":
            row["error"] = "all discovered team-news articles failed to load"
        elif status == "partial":
            row["error"] = "some discovered team-news articles failed; prior evidence retained"
        return documents, row
    except RuntimeError as exc:
        # Keep bounded prior evidence through a transient index outage. Its
        # publication time is rechecked by the pipeline before reuse.
        return [], {**(prior or {}), "id": source["id"], "club": source["club"],
                    "publisher": source["publisher"], "url": source["url"],
                    "status": "error", "error": str(exc)[:240],
                    "_unchanged_articles": list((prior or {}).get("article_validators", {}))}


def fetch_all(sources: list[dict], *, workers: int = 6,
              prior_health: dict[str, dict] | None = None) -> tuple[list[dict], list[dict], set[str], set[str]]:
    documents, health, unchanged, unchanged_urls = [], [], set(), set()
    prior_health = prior_health or {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_source, source, prior=prior_health.get(source["id"])): source
                   for source in sources}
        for future in as_completed(futures):
            docs, row = future.result()
            if row.pop("_not_modified", False):
                unchanged.add(row["id"])
            unchanged_urls.update(row.pop("_unchanged_articles", []))
            documents.extend(docs); health.append(row)
    documents.sort(key=lambda row: (row["club"], row["source_id"], row["url"]))
    health.sort(key=lambda row: row["id"])
    return documents, health, unchanged, unchanged_urls
