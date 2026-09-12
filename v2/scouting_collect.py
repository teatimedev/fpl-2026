"""Public article discovery for scouting; separate from automatic injury inputs.

Read publisher-supplied header dates and hydrated links, never dates guessed
from URLs, search snippets, related stories or HTTP Last-Modified headers.
"""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from .news_fetch import _request
from .public_article import ScoutingPage, parse_article, dated

EXCLUDE = re.compile(r'\b(?:women|womens|wsl|academy|u18s?|u21s?|under-18|under-21|b-team|tickets?|hospitality|easports|fc-27)\b', re.I)
RELEVANT = re.compile(r'report|reaction|press|conference|team.news|injur|preview|interview|line.up|analysis|radar|player.rating|all.you.need', re.I)


def candidate_urls(raw, base, names=()):
    page = ScoutingPage(); page.feed(raw)
    candidates = {}
    for href, label in page.links:
        url = urljoin(base, href).split('#', 1)[0].split('?', 1)[0]
        parsed = urlparse(url)
        text = label + ' ' + parsed.path
        if parsed.scheme != 'https' or parsed.netloc != urlparse(base).netloc or EXCLUDE.search(text):
            continue
        if re.search(r'/category/|/tag/|/gallery/|/video/|/citytv/|report-abuse', parsed.path):
            continue
        if not RELEVANT.search(text) or len(parsed.path.split('/')) < 4:
            continue
        score = sum(name.casefold() in text.casefold() for name in names) * 10
        score += 3 * bool(re.search('report|reaction|analysis|radar', text, re.I))
        candidates[url] = max(score, candidates.get(url, 0))
    return sorted(candidates, key=lambda u: (-candidates[u], u))


def collect_source(source, names=(), limit=5):
    if not source.get('enabled', True):
        return [], dict(source_id=source['id'], status='unsupported', articles=0)
    docs, errors = [], 0
    now = datetime.now(timezone.utc)
    try:
        raw, _ = _request(source['url'], attempts=1)
        urls = candidate_urls(raw, source['url'], names)[:limit]
        for url in urls:
            try:
                body, _ = _request(url, attempts=1)
                doc = parse_article(body, dict(source_id=source['id'], publisher=source['publisher'],
                                               url=url, club=source.get('club'),
                                               origin_group=urlparse(source['url']).hostname))
                if not EXCLUDE.search(doc['title']): docs.append(doc)
                published = dated(doc['published_at'])
                if published and now-timedelta(days=7) <= published <= now:
                    for linked in candidate_urls(body, url, names):
                        if linked not in urls and len(urls) < limit: urls.append(linked)
            except (RuntimeError, ValueError): errors += 1
        recent = sum(bool(dated(d['published_at']) and now - timedelta(days=7) <= dated(d['published_at']) <= now) for d in docs)
        status = 'partial' if recent and errors else 'ok' if recent else 'no_recent_articles'
        return docs, dict(source_id=source['id'], publisher=source['publisher'], url=source['url'],
                          status=status, discovered=len(urls), articles=recent, errors=errors)
    except (RuntimeError, ValueError):
        return [], dict(source_id=source['id'], status='error', articles=0)
