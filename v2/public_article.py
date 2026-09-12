"""Shared public article text, dates and hydrated links for both collectors."""
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


class PageParser(HTMLParser):
    BLOCKS = {'article', 'section', 'header', 'footer', 'nav', 'aside', 'div',
              'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol',
              'blockquote', 'table', 'tr', 'td', 'br', 'hr'}
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
        if tag in self.BLOCKS and not self._skip:
            self.parts.append('\n')
        if tag == "title":
            self._in_title = True
        if (tag == "time" and attributes.get('itemprop') == 'datePublished'
                and attributes.get("datetime") and not self.published_at):
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
        if tag in self.BLOCKS and not self._skip:
            self.parts.append('\n')
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
        clean = re.sub(r"\s+", " ", html.unescape(data))
        if clean:
            self.parts.append(clean)
            if self._in_title:
                self.title = (self.title + " " + clean).strip()
            if self._href:
                self._anchor.append(clean)

    @property
    def text(self):
        # Inline tags do not create sentence boundaries or merge unrelated
        # block paragraphs. Preserve the spaces supplied by the publisher.
        return '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())


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
            identity = item.get('url') or item.get('mainEntityOfPage') or ''
            if isinstance(identity, dict): identity = identity.get('@id', '')
            if (str(identity).rstrip('/') == source['url'].rstrip('/')
                    or (len(articles) == 1 and not identity)):
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
    if content.text.strip(): body = content.text
    return {**source, 'text': body, 'title': page.title,
            'published_at': published.isoformat() if published else None,
            'date_source': date_source if published else None}

