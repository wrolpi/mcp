from wrolpi_mcp import render
from wrolpi_mcp.config import API_BASE_URL

from .conftest import ARCHIVE_FG, DOC_FG, EPUB_FG, VIDEO_FG


def test_wrolpi_link_per_kind():
    assert render.wrolpi_link(VIDEO_FG) == '/videos/11'
    assert render.wrolpi_link(ARCHIVE_FG) == '/archives/22'
    assert render.wrolpi_link(DOC_FG) == '/docs/33'
    assert render.wrolpi_link(EPUB_FG) == '/docs/44'
    # An ebook with no FileGroup id opens in the reader; anything else is served from /media.
    assert render.wrolpi_link({'mimetype': 'application/epub+zip', 'primary_path': 'b/x y.epub'}) \
           == '/epub/epub.html?url=/media/b/x%20y.epub'
    assert render.wrolpi_link({'mimetype': 'image/png', 'primary_path': 'pics/a#1.png'}) == '/media/pics/a%231.png'
    assert render.wrolpi_link({'mimetype': 'image/png'}) is None


def test_file_group_kind():
    assert render.file_group_kind(VIDEO_FG) == 'video'
    assert render.file_group_kind({'model': 'ebook'}) == 'doc'
    assert render.file_group_kind({'mimetype': 'image/jpeg'}) == 'image'
    assert render.file_group_kind({'mimetype': 'application/zip'}) == 'file'


def test_format_duration_and_date():
    assert render.format_duration(754) == '12:34'
    assert render.format_duration(3661) == '1:01:01'
    assert render.format_duration(None) is None
    assert render.format_duration(-1) is None
    assert render.format_date('2024-05-01T12:00:00+00:00') == '2024-05-01'
    assert render.format_date(None) is None


def test_format_file_group_video():
    item = render.format_file_group(VIDEO_FG)
    assert item['kind'] == 'video'
    assert item['link'] == '/videos/11'
    assert item['duration'] == '12:34'
    assert item['channel'] == 'Example Channel'
    assert item['has_captions'] is True
    assert item['published'] == '2024-05-01'
    # The source URL is left out unless asked for, so the model shows the WROLPi link.
    assert 'url' not in item
    assert 'mimetype' not in item
    detail = render.format_file_group(VIDEO_FG, detail=True, include_url=True)
    assert detail['url'] == 'https://example.com/watch?v=abc'
    assert detail['mimetype'] == 'video/mp4'
    assert detail['path'] == 'videos/Example Channel/rain.mp4'


def test_format_file_group_doc_detail():
    item = render.format_file_group(DOC_FG, detail=True, doc={'subject': 'Water', 'page_count': 12,
                                                             'description': 'x' * 2000, 'language': 'en'})
    assert item['subject'] == 'Water'
    assert item['pages'] == 12
    assert item['language'] == 'en'
    assert len(item['description']) == render.LISTING_DESCRIPTION_LENGTH + 1
    assert item['description'].endswith('…')


def test_render_item_puts_link_first():
    text = render.render_item(render.format_file_group(VIDEO_FG))
    first, second = text.splitlines()[:2]
    assert first == f'Title: Catching rain water  —  LINK: {API_BASE_URL}/videos/11'
    assert second.startswith('ID: 11')
    assert 'Tags: water' in text
    assert 'Source URL' not in text


def test_render_file_groups_hint():
    assert render.render_file_groups([], 0, searched=True) == render.EMPTY_SEARCH_HINT
    assert render.render_file_groups([], 0, searched=False) == 'No results found.'
    text = render.render_file_groups([VIDEO_FG, DOC_FG], 57)
    assert text.count('--- Result ') == 2
    assert text.endswith('Total matching: 57')


def test_page_text():
    text = 'a' * 120
    assert render.page_text(text, 0, max_chars=50).startswith('a' * 50 + '\n\n[Truncated')
    assert 'offset=50' in render.page_text(text, 0, max_chars=50)
    assert render.page_text(text, 100, max_chars=50) == 'a' * 20
    assert render.page_text(text, 500).startswith('[No more content')
    assert render.page_text('', 0) == ''


def test_html_to_text():
    html = '<html><head><style>x{}</style><script>bad()</script></head><body><nav>menu</nav>' \
           '<h1>Title</h1><p>One</p>\n\n\n<p>Two</p><footer>foot</footer></body></html>'
    assert render.html_to_text(html) == 'Title\nOne\nTwo'


def test_format_caption_chunks_and_comments():
    chunks = [{'start_seconds': 0, 'text': 'hello'}, {'start_seconds': 3725.9, 'text': 'bye'}]
    assert render.format_caption_chunks(chunks) == '[00:00:00] hello\n[01:02:05] bye'
    assert render.format_caption_chunks(None) == ''
    comments = [{'author': 'a', 'text': 'top', 'like_count': 2, 'parent': 'root'},
                {'author': None, 'text': ' reply ', 'parent': 'abc'}]
    assert render.format_comments(comments) == 'a: top  (2 likes)\n    anonymous: reply'


def test_render_zim_search():
    results = [{'id': 5, 'metadata': {'title': 'Wikipedia'}, 'estimate': 3,
                'search': [{'zim_id': 5, 'path': 'A/Rain water', 'title': 'Rain water', 'headline': 'x <b>rain</b>'}]},
               {'metadata': {'title': 'Wiktionary'}, 'estimate': 0, 'search': []}]
    text = render.render_zim_search(results)
    assert f'LINK: {API_BASE_URL}/api/zim/5/entry/A/Rain%20water' in text
    assert 'Zim: Wikipedia' in text
    assert 'Zim ID: 5 | Path: A/Rain water' in text
    assert text.endswith('Total matching: 3')
    assert render.render_zim_search([]) == 'No results found.'


def test_format_size():
    assert render.format_size(None) == '0 B'
    assert render.format_size(2 * 1024 ** 2) == '2.0 MB'
    assert render.format_size(3 * 1024 ** 3) == '3.00 GB'
