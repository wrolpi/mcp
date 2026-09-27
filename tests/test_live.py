"""Smoke test against a real WROLPi.  Skipped unless WROLPI_LIVE_URL is set:

    WROLPI_LIVE_URL=https://wrolpi.local:8443 uv run pytest tests/test_live.py
"""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('WROLPI_LIVE_URL'), reason='WROLPI_LIVE_URL is not set')


@pytest.fixture(autouse=True)
def live_url(monkeypatch):
    from wrolpi_mcp import client, config, render
    url = os.environ['WROLPI_LIVE_URL'].rstrip('/')
    monkeypatch.setattr(config, 'API_BASE_URL', url)
    monkeypatch.setattr(client, 'API_BASE_URL', url)
    monkeypatch.setattr(render, 'API_BASE_URL', url)
    return url


async def test_every_tool_answers(live_url):
    from wrolpi_mcp import server
    assert 'version' in await server.get_status()
    assert 'file_statistics' in await server.get_statistics()
    assert await server.list_tags()
    assert await server.list_collections(limit=3)
    assert await server.list_zim_files()
    assert await server.list_files()
    assert await server.list_downloads(limit=2)
    assert await server.get_map_overview()
    assert await server.get_inventory()
    assert await server.search_places('a', limit=1)
    results = await server.search_files(limit=2)
    assert 'LINK: ' in results or results == 'No results found.'
