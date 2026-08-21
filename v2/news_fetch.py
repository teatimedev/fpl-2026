"""Bounded public HTTP collector for official club news pages."""
from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser


UA = "fpl-2026-team-news/1.0 (+https://github.com/teatimedev/fpl-2026)"
RELEVANT = re.compile(r"team.news|injur|fitness|press|conference|squad|availability|preview", re.I)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []
        self.title: str = ""
        self.published_at: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "time" and attributes.get("datetime") and not self.published_at:
            self.published_at = attributes["datetime"]
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key in {"article:published_time", "date", "datepublished", "publish-date"}:
                self.published_at = attributes.get("content") or self.published_at
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip += 1
        if not self._skip and tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "svg", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            label = " ".join(self._anchor).strip()
            self.links.append((self._href, label))
            self._href = None
            self._anchor = []

    def handle_data(self, data):
        if self._skip:
            return
        clean = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if clean:
            self.parts.append(clean)
            if self._in_title:
                self.title = (self.title + " " + clean).strip()
            if self._href:
                self._anchor.append(clean)

    @property
    def text(self):
        return "\n".join(self.parts)


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
                 prior: dict | None = None) -> tuple[list[dict], dict]:
    if not source["enabled"]:
        return [], {"id": source["id"], "club": source["club"], "status": "unsupported",
                    "error": source.get("unsupported_reason")}
    try:
        body, headers = _request(source["url"], conditional=prior)
        if body is None and prior:
            return [], {**prior, "_not_modified": True}
        parser = PageParser(); parser.feed(body)
        urls = []
        for href, label in parser.links:
            absolute = urllib.parse.urljoin(source["url"], href).split("#", 1)[0]
            if _same_site(source["url"], absolute) and RELEVANT.search(label + " " + absolute):
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
        prior_articles = (prior or {}).get("article_validators") or {}
        for url in urls:
            try:
                article, article_headers = _request(url, conditional=prior_articles.get(url))
                if article is None:
                    unchanged_articles.append(url)
                    article_validators[url] = prior_articles.get(url, {})
                    continue
                article_validators[url] = {k: v for k, v in article_headers.items() if v}
                page = PageParser(); page.feed(article)
                if not page.published_at:
                    published = re.search(r'"datePublished"\s*:\s*"([^"]+)"', article, re.I)
                    if published:
                        page.published_at = published.group(1)
                title = page.title or next((part for part in page.parts if len(part) >= 8), url)
                documents.append({
                    "source_id": source["id"], "publisher": source["publisher"], "club": source["club"],
                    "url": url, "title": title[:160], "text": page.text,
                    "published_at": page.published_at,
                })
            except RuntimeError as exc:
                article_errors.append(str(exc))
        digest = hashlib.sha256("\n".join(d["text"] for d in documents).encode()).hexdigest()
        status = "error" if urls and len(documents) == 1 and article_errors else "ok"
        row = {
            "id": source["id"], "club": source["club"], "publisher": source["publisher"],
            "url": source["url"], "status": status, "documents": len(documents),
            "article_errors": len(article_errors), "content_hash": digest,
            "article_validators": article_validators,
            **{k: v for k, v in headers.items() if v},
        }
        if unchanged_articles:
            row["_unchanged_articles"] = unchanged_articles
        if status == "error":
            row["error"] = "all discovered team-news articles failed to load"
        return documents, row
    except RuntimeError as exc:
        return [], {"id": source["id"], "club": source["club"], "publisher": source["publisher"],
                    "url": source["url"], "status": "error", "error": str(exc)[:240]}


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
