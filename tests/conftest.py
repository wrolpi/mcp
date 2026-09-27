"""Shared fixtures: respx mocks of the WROLPi endpoints with compact, hand-written response
shapes modeled on the real API (FileGroup.__json__ etc.)."""
import pytest
import respx
from httpx import Response

from wrolpi_mcp.config import API_BASE_URL

VIDEO_FG = {
    'id': 11, 'model': 'video', 'mimetype': 'video/mp4', 'title': 'Catching rain water',
    'primary_path': 'videos/Example Channel/rain.mp4', 'directory': 'videos/Example Channel',
    'published_datetime': '2024-05-01T12:00:00+00:00', 'length': 754, 'size': 12_345_678,
    'tags': ['water'], 'url': 'https://example.com/watch?v=abc', 'author': None,
    'video': {'id': 1, 'channel_id': 3, 'channel': {'id': 3, 'name': 'Example Channel'},
              'caption_files': [{'path': 'videos/Example Channel/rain.en.vtt'}], 'have_comments': True,
              'view_count': 1200},
}
ARCHIVE_FG = {
    'id': 22, 'model': 'archive', 'mimetype': 'text/html', 'title': 'Rain barrels explained',
    'primary_path': 'archive/example.com/2024-01-01-rain.html', 'directory': 'archive/example.com',
    'published_datetime': '2024-01-01T00:00:00+00:00', 'size': 50_000, 'tags': [],
    'url': 'https://example.com/rain',
    'data': {'singlefile_path': '2024-01-01-rain.html', 'readability_txt_path': '2024-01-01-rain.readability.txt'},
}
DOC_FG = {
    'id': 33, 'model': 'doc', 'mimetype': 'application/pdf', 'title': 'Water Storage Guide',
    'primary_path': 'docs/water.pdf', 'published_datetime': None, 'size': 1_000, 'tags': ['water'],
    'author': 'A. Author', 'url': None,
}
EPUB_FG = {
    'id': 44, 'model': 'doc', 'mimetype': 'application/epub+zip', 'title': 'An Ebook',
    'primary_path': 'books/An Ebook/An Ebook.epub', 'size': 10, 'tags': [],
}


@pytest.fixture
def api():
    """A respx router bound to the configured WROLPi URL.  Tests add routes to it."""
    with respx.mock(base_url=API_BASE_URL, assert_all_called=False) as router:
        yield router


def json_route(router, method: str, path: str, payload, status: int = 200):
    return router.request(method, path).mock(return_value=Response(status, json=payload))


def text_route(router, path: str, text: str, status: int = 200):
    return router.get(path).mock(return_value=Response(status, text=text))
