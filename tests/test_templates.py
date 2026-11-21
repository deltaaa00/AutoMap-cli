"""Check assembled pages and their local assets after splitting frontend files.

Flask must expand every Jinja include and serve every referenced script/stylesheet.
Browser suites exercise actual initialization order and behavior; these checks
catch missing files, duplicate script registration and accidental inline bundles.
"""
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest


class PageAssets(HTMLParser):
    """Collect executable script and stylesheet references from rendered HTML."""

    def __init__(self):
        super().__init__()
        self.scripts = []
        self.stylesheets = []
        self.inline_scripts = []
        self.inline_styles = []
        self._inline_tag = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            if attrs.get('src'):
                self.scripts.append(attrs['src'])
            elif attrs.get('type') not in ('application/json', 'application/ld+json'):
                self._inline_tag = 'script'
        elif tag == 'style':
            self._inline_tag = 'style'
        elif tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.stylesheets.append(attrs['href'])

    def handle_data(self, data):
        if self._inline_tag == 'script' and data.strip():
            self.inline_scripts.append(data)
        elif self._inline_tag == 'style' and data.strip():
            self.inline_styles.append(data)

    def handle_endtag(self, tag):
        if tag == self._inline_tag:
            self._inline_tag = None


@pytest.mark.parametrize('path', ['/', '/studio', '/api-manager'])
def test_page_assembly_serves_all_local_assets(web, path):
    client = web.app.test_client()
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '{% include' not in html
    assets = PageAssets()
    assets.feed(html)
    assert len(assets.scripts) == len(set(assets.scripts)), 'A script was loaded twice.'
    assert not assets.inline_scripts, 'Move executable page scripts into static files.'
    assert not assets.inline_styles, 'Move page stylesheets into static files.'

    for url in assets.scripts + assets.stylesheets:
        if urlsplit(url).netloc:
            continue
        asset = client.get(url)
        assert asset.status_code == 200, url
        assert asset.data.strip(), url
        assert 'text/html' not in asset.content_type, url
