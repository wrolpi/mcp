"""Each tool against mocked WROLPi endpoints: the right endpoint is called with the right body,
and the rendered text carries what a model needs (IDs, links, hints, continuation offsets)."""
import json

import httpx
import pytest
from httpx import Response

from wrolpi_mcp import server
from wrolpi_mcp.config import API_BASE_URL

from .conftest import ARCHIVE_FG, DOC_FG, VIDEO_FG, json_route, text_route

SEARCH_RESULT = {'file_groups': [VIDEO_FG, ARCHIVE_FG], 'totals': {'file_groups': 2}}
EMPTY_RESULT = {'file_groups': [], 'totals': {'file_groups': 0}}


async def test_tool_set():
    """The deliberate tool set: kind-generic library tools plus read-only listings."""
    tools = await server.mcp.list_tools()
    assert {i.name for i in tools} == {
        'search_files', 'get_file', 'read_content',
        'search_zim', 'get_zim_entry', 'list_zim_files',
        'list_collections', 'list_tags', 'list_files', 'read_file',
        'list_downloads', 'get_map_overview', 'search_places',
        'get_statistics', 'get_inventory', 'get_status',
    }
    assert all(i.annotations.read_only_hint for i in tools)


async def test_search_files_global(api):
    route = json_route(api, 'POST', '/api/files/search', SEARCH_RESULT)
    text = await server.search_files(query='rain', tag_names=['water'], limit=5)
    body = json.loads(route.calls.last.request.content)
    assert body == {'search_str': 'rain', 'tag_names': ['water'], 'limit': 5, 'offset': 0, 'headline': True, 'deep': False}
    assert f'LINK: {API_BASE_URL}/videos/11' in text
    assert f'LINK: {API_BASE_URL}/archives/22' in text
    assert text.endswith('Total matching: 2')


async def test_search_files_deep_and_limits(api):
    route = json_route(api, 'POST', '/api/files/search', SEARCH_RESULT)
    await server.search_files(query='rain', deep=True, limit=5000, offset=-3)
    body = json.loads(route.calls.last.request.content)
    assert body['deep'] is True
    assert body['limit'] == server.MAX_LIMIT
    assert body['offset'] == 0


async def test_search_files_empty_hint(api):
    json_route(api, 'POST', '/api/files/search', EMPTY_RESULT)
    assert 'No matches' in await server.search_files(query='xyzzy')
    assert await server.search_files() == 'No results found.'


async def test_search_files_by_kind(api):
    videos = json_route(api, 'POST', '/api/videos/search', SEARCH_RESULT)
    archives = json_route(api, 'POST', '/api/archive/search', SEARCH_RESULT)
    docs = json_route(api, 'POST', '/api/docs/search', SEARCH_RESULT)
    await server.search_files(kind='video', query='a')
    await server.search_files(domain='example.com')
    await server.search_files(author='Smith', subject='Water', query='b')
    assert videos.called and archives.called and docs.called
    assert json.loads(archives.calls.last.request.content)['domain'] == 'example.com'
    assert json.loads(docs.calls.last.request.content) == \
           {'search_str': 'b', 'author': 'Smith', 'subject': 'Water', 'limit': 10, 'offset': 0, 'deep': False}
    assert 'Unknown kind' in await server.search_files(kind='zim')


async def test_search_files_channel_resolution(api):
    channels = json_route(api, 'POST', '/api/collections/search', {'collections': [
        {'id': 3, 'name': 'Example Channel', 'kind': 'channel'},
        {'id': 4, 'name': 'Example Channel Two', 'kind': 'channel'}]})
    videos = json_route(api, 'POST', '/api/videos/search', SEARCH_RESULT)

    # An exact name wins over a longer partial match.
    await server.search_files(channel='example channel', query='rain')
    assert json.loads(channels.calls.last.request.content) == {'kind': 'channel', 'search_str': 'example channel'}
    assert json.loads(videos.calls.last.request.content)['channel_id'] == 3

    # A numeric channel is an id and needs no lookup.
    await server.search_files(channel='4')
    assert json.loads(videos.calls.last.request.content)['channel_id'] == 4
    assert channels.call_count == 1

    # Ambiguous names list the candidates instead of guessing.
    text = await server.search_files(channel='Example')
    assert 'Several channels match' in text and '(ID: 3)' in text and '(ID: 4)' in text

    api.post('/api/collections/search').mock(return_value=Response(200, json={'collections': []}))
    assert 'No channel matches' in await server.search_files(channel='nothing')


async def test_get_file_tries_each_kind(api):
    api.get('/api/videos/22').mock(return_value=Response(404, json={'error': 'no'}))
    json_route(api, 'GET', '/api/archive/22', {'file_group': ARCHIVE_FG, 'history': [
        {'id': 21, 'title': 'Older snapshot', 'published_datetime': '2023-01-01T00:00:00+00:00'}]})
    text = await server.get_file(22)
    assert f'LINK: {API_BASE_URL}/archives/22' in text
    assert 'Source URL: https://example.com/rain' in text
    assert 'Earlier snapshots (1):' in text and '(ID: 21, 2023-01-01)' in text


async def test_get_file_doc_and_missing(api):
    docs = json_route(api, 'GET', '/api/docs/33', {'file_group': DOC_FG, 'doc': {'page_count': 9, 'subject': 'Water'}})
    text = await server.get_file(33, kind='doc')
    assert 'Pages: 9' in text and 'Subject: Water' in text
    assert docs.calls.last.request.url.params['skip_viewed'] == 'true'

    api.get('/api/videos/99').mock(return_value=Response(404, json={}))
    api.get('/api/archive/99').mock(return_value=Response(404, json={}))
    # Docs answer 400 for an unknown id.
    api.get('/api/docs/99').mock(return_value=Response(400, json={}))
    assert 'No video, archived page, or document has ID 99' in await server.get_file(99)


async def test_read_content_captions_paged(api):
    chunks = [{'start_seconds': i, 'text': 'word ' * 20} for i in range(1000)]
    json_route(api, 'GET', '/api/videos/11/captions', {'captions': chunks})
    text = await server.read_content(11)
    assert text.startswith('[00:00:00] word')
    assert '[Truncated' in text and f'offset={server.render.MAX_CONTENT_CHARS}' in text
    more = await server.read_content(11, offset=server.render.MAX_CONTENT_CHARS)
    assert not more.startswith('[00:00:00]')

    json_route(api, 'GET', '/api/videos/12/captions', {'captions': None})
    assert 'has no captions' in await server.read_content(12)


async def test_read_content_comments(api):
    json_route(api, 'GET', '/api/videos/11/comments', {'comments': [{'author': 'x', 'text': 'nice', 'parent': 'root'}]})
    assert await server.read_content(11, part='comments') == 'x: nice'
    assert 'part must be' in await server.read_content(11, part='likes')


async def test_read_content_archive_text(api):
    api.get('/api/videos/22/captions').mock(return_value=Response(404, json={}))
    json_route(api, 'GET', '/api/archive/22', {'file_group': ARCHIVE_FG, 'history': []})
    readability = text_route(api, '/media/archive/example.com/2024-01-01-rain.readability.txt', 'Plain text.')
    assert await server.read_content(22) == 'Plain text.'
    assert readability.called

    # Without the readability text the singlefile HTML is stripped.
    api.get('/media/archive/example.com/2024-01-01-rain.readability.txt').mock(return_value=Response(404))
    text_route(api, '/media/archive/example.com/2024-01-01-rain.html', '<html><body><p>From HTML</p></body></html>')
    assert await server.read_content(22) == 'From HTML'

    api.get('/api/videos/33/captions').mock(return_value=Response(404, json={}))
    api.get('/api/archive/33').mock(return_value=Response(404, json={}))
    assert 'only videos with captions and archived pages' in await server.read_content(33)


async def test_zim_tools(api):
    json_route(api, 'GET', '/api/zim/', {'zims': [
        {'id': 5, 'path': 'zims/wikipedia.zim', 'size': 3 * 1024 ** 3, 'auto_search': True,
         'metadata': {'title': 'Wikipedia', 'creator': 'Kiwix', 'description': 'All of it'}}]})
    text = await server.list_zim_files()
    assert 'ID: 5 | Wikipedia' in text and 'Size: 3.00 GB' in text and 'Searched by default: yes' in text

    search_all = json_route(api, 'POST', '/api/zim/search', {'zims': [
        {'id': 5, 'metadata': {'title': 'Wikipedia'}, 'estimate': 1,
         'search': [{'zim_id': 5, 'path': 'A/Rain', 'title': 'Rain', 'headline': 'wet'}]}]})
    text = await server.search_zim('rain', limit=3)
    assert json.loads(search_all.calls.last.request.content) == {'search_str': 'rain', 'limit': 3, 'offset': 0}
    assert f'LINK: {API_BASE_URL}/api/zim/5/entry/A/Rain' in text

    json_route(api, 'POST', '/api/zim/search/5', {'zim': {
        'metadata': {'title': 'Wikipedia'}, 'estimate': 1,
        'search': [{'path': 'A/Rain', 'title': 'Rain'}]}})
    assert 'Zim ID: 5 | Path: A/Rain' in await server.search_zim('rain', zim_id=5)
    api.post('/api/zim/search/6').mock(return_value=Response(404, json={}))
    assert 'Zim ID 6 not found' in await server.search_zim('rain', zim_id=6)

    text_route(api, '/api/zim/5/entry/A/Rain%20water', '<html><body><h1>Rain water</h1><p>Falls.</p></body></html>')
    text = await server.get_zim_entry(5, 'A/Rain water')
    assert text == f'LINK: {API_BASE_URL}/api/zim/5/entry/A/Rain%20water\n\nRain water\nFalls.'
    api.get('/api/zim/5/entry/nope').mock(return_value=Response(404))
    assert 'has no entry' in await server.get_zim_entry(5, 'nope')


async def test_list_collections_paged(api):
    collections = [{'id': i, 'name': f'Channel {i}', 'kind': 'channel', 'directory': f'videos/c{i}',
                    'item_count': i} for i in range(1, 6)]
    route = json_route(api, 'POST', '/api/collections/search', {'collections': collections})
    text = await server.list_collections(kind='channel', query='Chan', limit=2)
    assert json.loads(route.calls.last.request.content) == {'kind': 'channel', 'search_str': 'Chan'}
    assert 'ID: 1 | Channel 1 (channel)' in text and 'ID: 3' not in text
    assert 'Total: 5' in text and 'offset=2' in text
    text = await server.list_collections(kind='channel', limit=2, offset=4)
    assert 'ID: 5' in text and 'More available' not in text
    api.post('/api/collections/search').mock(return_value=Response(200, json={'collections': []}))
    assert await server.list_collections() == 'No collections found.'


async def test_list_tags(api):
    json_route(api, 'GET', '/api/tag', {'tags': [
        {'name': 'water', 'file_group_count': 3, 'zim_entry_count': 0, 'channel_count': 1, 'domain_count': 0},
        {'name': 'unused', 'file_group_count': 0}]})
    json_route(api, 'GET', '/api/tag/recent', {'tag_names': ['water']})
    text = await server.list_tags()
    assert 'Tags (2):' in text
    assert '  water  (3 files, 1 channels)' in text
    assert '  unused  (unused)' in text
    assert text.endswith('Recently used: water')


async def test_list_files_walks_tree(api):
    tree = {'files': {
        'videos/': {'path': 'videos/', 'children': {
            'Example Channel/': {'path': 'videos/Example Channel/', 'children': {
                'rain.mp4': {'path': 'videos/Example Channel/rain.mp4', 'size': 2 * 1024 ** 2, 'mimetype': 'video/mp4'},
                'notes.txt': {'path': 'videos/Example Channel/notes.txt', 'size': 12, 'mimetype': 'text/plain'},
                'sub/': {'path': 'videos/Example Channel/sub/'},
            }},
        }},
        'archive/': {'path': 'archive/'},
        'readme.txt': {'path': 'readme.txt', 'size': 5, 'mimetype': 'text/plain'},
    }}
    route = json_route(api, 'POST', '/api/files/', tree)
    text = await server.list_files('videos/Example Channel/')
    assert json.loads(route.calls.last.request.content) == {'directories': ['videos/Example Channel']}
    lines = text.splitlines()
    assert lines[0] == 'Directory: videos/Example Channel'
    assert lines[1] == '  [dir]  videos/Example Channel/sub/'
    assert '  [file] videes' not in text
    assert '  [file] videos/Example Channel/notes.txt  (text/plain, 12 B)' in text
    assert '  [file] videos/Example Channel/rain.mp4  (video/mp4, 2.0 MB)' in text
    assert 'Total entries: 3' in text

    top = await server.list_files()
    assert json.loads(route.calls.last.request.content) == {'directories': []}
    assert top.splitlines()[1:3] == ['  [dir]  archive/', '  [dir]  videos/']
    assert 'No such directory: videos/nope' == await server.list_files('videos/nope')
    api.post('/api/files/').mock(return_value=Response(500, json={}))
    assert 'No such directory' in await server.list_files('nope')


async def test_list_files_paged(api):
    children = {f'f{i:03d}.txt': {'path': f'd/f{i:03d}.txt', 'size': 1, 'mimetype': 'text/plain'} for i in range(150)}
    json_route(api, 'POST', '/api/files/', {'files': {'d/': {'path': 'd/', 'children': children}}})
    text = await server.list_files('d')
    assert 'f099.txt' in text and 'f100.txt' not in text
    assert f'offset={server.LIST_PAGE_SIZE}' in text
    text = await server.list_files('d', offset=100)
    assert 'f100.txt' in text and 'More entries' not in text


async def test_read_file(api):
    info = json_route(api, 'POST', '/api/files/file', {'file': {'path': 'notes/a b.txt', 'size': 5, 'mimetype': 'text/plain'}})
    text_route(api, '/media/notes/a%20b.txt', 'hello')
    assert await server.read_file('/notes/a b.txt') == 'hello'
    assert json.loads(info.calls.last.request.content) == {'file': 'notes/a b.txt', 'skip_tracking': True}

    json_route(api, 'POST', '/api/files/file', {'file': {'mimetype': 'video/mp4', 'size': 5}})
    assert 'not a text file' in await server.read_file('v.mp4')
    json_route(api, 'POST', '/api/files/file', {'file': {'mimetype': 'text/plain', 'size': server.MAX_TEXT_FILE_SIZE + 1}})
    assert 'too large' in await server.read_file('big.txt')
    api.post('/api/files/file').mock(return_value=Response(404, json={}))
    assert 'no such file' in await server.read_file('nope.txt')


async def test_list_downloads(api):
    json_route(api, 'GET', '/api/download', {
        'recurring_downloads': [
            {'id': 1, 'url': 'https://example.com/feed', 'status': 'complete', 'downloader': 'rss',
             'frequency': 3600, 'next_download': '2026-01-01T00:00:00+00:00', 'destination': 'videos/x',
             'tag_names': ['water']},
            {'id': 2, 'url': 'https://example.com/other', 'status': 'failed', 'downloader': 'rss',
             'frequency': 3600, 'error': 'Traceback ' + 'x' * 1000}],
        'once_downloads': [{'id': 3, 'url': 'https://example.com/v', 'status': 'pending', 'downloader': 'video'}],
        'pending_once_downloads': 1})
    json_route(api, 'GET', '/api/status', {'downloads': {'pending': 1, 'recurring': 2}})
    text = await server.list_downloads()
    assert text.startswith('Summary: pending=1, recurring=2')
    assert 'every 3600s, next: 2026-01-01T00:00:00+00:00' in text
    assert 'tags: water' in text
    assert 'One-time downloads (1 shown, 1 pending)' in text
    error_line = next(i for i in text.splitlines() if 'error:' in i)
    assert len(error_line) < server.render.DOWNLOAD_ERROR_LENGTH + 20

    text = await server.list_downloads(status='failed')
    assert '(ID: 2,' in text and '(ID: 1,' not in text and '(ID: 3,' not in text


async def test_map_tools(api):
    json_route(api, 'GET', '/api/map/files', {'files': [
        {'name': 'region.pmtiles', 'size': 1024 ** 3, 'has_search_index': True}]})
    json_route(api, 'GET', '/api/map/subscribe', {'subscriptions': [{'name': 'Utah'}]})
    json_route(api, 'GET', '/api/map/pins', {'pins': [{'label': 'Home', 'lat': 1.5, 'lon': -2.5}]})
    text = await server.get_map_overview()
    assert '  region.pmtiles  (1.00 GB, searchable)' in text
    assert 'Subscribed regions: Utah' in text
    assert f'  Home  (1.5, -2.5)  LINK: {API_BASE_URL}/map?lat=1.5&lon=-2.5&z=12' in text

    search = json_route(api, 'GET', '/api/map/search', {'results': [
        {'name': 'Portland', 'kind': 'locality', 'kind_detail': 'city', 'region': 'Oregon', 'population': 652503,
         'lat': 45.5, 'lon': -122.6}], 'total': 1})
    text = await server.search_places('Port', lat=45.0, lon=-122.0)
    assert dict(search.calls.last.request.url.params) == {'q': 'Port', 'limit': '10', 'offset': '0', 'lat': '45.0', 'lon': '-122.0'}
    assert '1. Portland  (locality, city, Oregon, pop. 652,503)  at 45.5, -122.6  LINK:' in text
    json_route(api, 'GET', '/api/map/search', {'results': [], 'total': 0})
    assert 'No places found' in await server.search_places('zzz')


async def test_inventory_status_statistics(api):
    json_route(api, 'GET', '/api/inventory/', {'inventories': [
        {'slug': 'food', 'name': 'Food Storage', 'type': 'food', 'items': [{'id': 1}, {'id': 2}]}]})
    text = await server.get_inventory()
    assert 'Slug: food | Food Storage' in text and 'Items: 2' in text and f'LINK: {API_BASE_URL}/inventory' in text
    assert json.loads(await server.get_inventory('food'))['slug'] == 'food'
    assert 'No inventory has slug "x"' in await server.get_inventory('x')

    json_route(api, 'GET', '/api/status', {'version': '0.28.7\n', 'wrol_mode': False, 'processes_stats': [1] * 500,
                                            'flags': {'db_up': True}})
    status = json.loads(await server.get_status())
    assert status == {'version': '0.28.7', 'wrol_mode': False, 'flags': {'db_up': True}}

    json_route(api, 'GET', '/api/statistics', {'file_statistics': {'video_count': 3}})
    assert json.loads(await server.get_statistics()) == {'file_statistics': {'video_count': 3}}


async def test_http_errors_are_explained(api):
    """A failed request comes back as text naming the status and the WROLPi's own error, not a bare exception."""
    api.post('/api/files/search').mock(return_value=Response(503, json={'code': 'DB_DOWN', 'message': 'no database'}))
    text = await server.search_files(query='x')
    assert '503' in text and 'DB_DOWN' in text and 'no database' in text


async def test_timeout_is_explained(api):
    """httpx timeouts have an empty str(); without translation a model sees only "Error executing tool"."""
    api.post('/api/zim/search').mock(side_effect=httpx.ReadTimeout('read timed out'))
    text = await server.search_zim(query='x')
    assert 'did not answer within' in text and 'WROLPI_TIMEOUT' in text and 'zim_id' in text

    api.post('/api/files/search').mock(side_effect=httpx.ReadTimeout(''))
    text = await server.search_files(query='x', deep=True)
    assert 'did not answer within' in text and 'deep' in text


async def test_connection_error_names_the_address(api):
    api.get('/api/statistics').mock(side_effect=httpx.ConnectError('connection refused'))
    text = await server.get_statistics()
    assert API_BASE_URL in text and 'WROLPI_API_URL' in text


async def test_non_json_answer_is_explained(api):
    """Caddy redirects to the fallback UI while the API is down: a 200 HTML page, not JSON."""
    api.get('/api/statistics').mock(return_value=Response(200, text='<html>fallback</html>'))
    text = await server.get_statistics()
    assert 'other than JSON' in text


async def test_unrelated_exceptions_still_raise(api):
    api.get('/api/statistics').mock(side_effect=RuntimeError('bug'))
    with pytest.raises(RuntimeError):
        await server.get_statistics()
