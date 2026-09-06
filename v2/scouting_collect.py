"""Public article discovery for scouting; separate from automatic injury inputs.

Read publisher-supplied header dates and hydrated links, never dates guessed
from URLs, search snippets, related stories or HTTP Last-Modified headers.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from .news_fetch import PageParser, _request

EXCLUDE = re.compile(r'\b(?:women|womens|wsl|academy|u18s?|u21s?|under-18|under-21|b-team|tickets?|hospitality|easports|fc-27)\b', re.I)
RELEVANT = re.compile(r'report|reaction|press|conference|team.news|injur|preview|interview|line.up|analysis|radar|player.rating|all.you.need', re.I)


def decode(value):
    return html.unescape(str(value)).replace(r'\u002B', '+').replace(r'\u002b', '+')


def dated(value):
    try:
        dt = datetime.fromisoformat(decode(value).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


class ScoutingPage(PageParser):
    def __init__(self):
        super().__init__()
        self.date_source = None
        self.blocked = False

    def handle_starttag(self, tag, attrs):
        prior_date = self.published_at
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        # Club pages also use <time> for fixture kickoffs and related stories.
        # A generic first time element is not an article publication date.
        if tag == 'time' and attrs.get('itemprop') != 'datePublished':
            self.published_at = prior_date
        component = attrs.get('data-component', '')
        if component == 'NewsHeader' and attrs.get('data-prop-date'):
            self.published_at = decode(attrs['data-prop-date'])
            self.date_source = 'publisher_article_header'
        raw = attrs.get('data-props')
        if not raw:
            return
        try:
            props = json.loads(raw)
        except ValueError:
            return
        if component == 'ArticleLoginOverlay' and props.get('requiresLogin'):
            self.blocked = True
        if component == 'ArticleHeader':
            self.published_at = decode(props.get('articleHeaderDetails', {}).get('date', '')) or self.published_at
            self.date_source = 'publisher_article_header'
        # Navigation and related-story dates must never date the current article.
        if re.search('News|Article', component) and not re.search('Header|Footer|Sidebar|Login|Dropdown', component):
            def links(node):
                if isinstance(node, dict):
                    if isinstance(node.get('url'), str):
                        self.links.append((node['url'], str(node.get('title', ''))))
                    for child in node.values(): links(child)
                elif isinstance(node, list):
                    for child in node: links(child)
            links(props)


class ArticleBody(PageParser):
    def __init__(self):
        super().__init__()
        self.article_depth = 0

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == 'article': self.article_depth += 1

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == 'article': self.article_depth = max(0, self.article_depth-1)

    def handle_data(self, data):
        if self.article_depth: super().handle_data(data)


def parse_article(raw, source):
    page = ScoutingPage(); page.feed(raw)
    if page.blocked:
        raise ValueError('article_requires_login')
    published = dated(page.published_at)
    date_source = page.date_source or 'publisher_meta_or_time'
    if not published:
        # JSON-LD can contain several related articles. Only accept a matching
        # canonical URL, or the page's sole Article object.
        articles = []
        def visit(node):
            if isinstance(node, dict):
                if 'Article' in str(node.get('@type', '')):
                    articles.append(node)
                for v in node.values(): visit(v)
            elif isinstance(node, list):
                for v in node: visit(v)
        for block in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', raw, re.S | re.I):
            try: visit(json.loads(html.unescape(block)))
            except ValueError: pass
        for item in articles:
            if item.get('url', '').rstrip('/') == source['url'].rstrip('/') or len(articles) == 1:
                published = dated(item.get('datePublished'))
                if published:
                    date_source = 'publisher_json_ld'; break
    if not published and urlparse(source['url']).hostname == 'www.mancity.com':
        byline = re.search(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2})\b', page.text)
        if byline:
            published = datetime.strptime(byline[1], '%d %b %Y, %H:%M').replace(tzinfo=ZoneInfo('Europe/London')).astimezone(timezone.utc)
            date_source = 'publisher_display_time_Europe_London'
    body = page.text
    content = ArticleBody(); content.feed(raw)
    if len(content.text) > 300: body = content.text
    return {**source, 'text': body, 'title': page.title,
            'published_at': published.isoformat() if published else None,
            'date_source': date_source if published else None}


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
