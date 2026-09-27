"""WROLPi MCP Server: exposes a WROLPi's offline library to LLMs via the Model Context Protocol.

Every tool is a read-only call to the WROLPi's public HTTP API (the same endpoints the WROLPi
web app uses), rendered as compact text with a WROLPi link on every item."""
import json
import logging
import sys

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from wrolpi_mcp import render
from wrolpi_mcp.client import api_get, api_get_text, api_post
from wrolpi_mcp.config import DEFAULT_LIMIT
from wrolpi_mcp.render import absolute_link, page_text

# All logging must go to stderr: stdout is the MCP stdio transport.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger('wrolpi_mcp')

mcp = MCPServer(
    'WROLPi',
    instructions=(
        'WROLPi is an offline digital library containing videos, archived web pages, ebooks, Zim '
        'encyclopedias (Wikipedia, etc.), maps, and documents. '
        'Use search_files to find content of any kind, get_file for details, and read_content '
        'for captions, page text, or comments. '
        'IMPORTANT: When presenting results to the user, ALWAYS include the "LINK" for every '
        'item. Never use the "Source URL"; only use the local WROLPi LINK. '
        'When calling tools, use the top-level ID (FileGroup ID).'
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)

# The maximum results any WROLPi search endpoint accepts.
MAX_LIMIT = 100
# Directory listings are paged by entry count so one big channel cannot fill the model's context.
LIST_PAGE_SIZE = 100
# Refuse to read files this large through read_file; real content is paged anyway.
MAX_TEXT_FILE_SIZE = 10 * 1024 * 1024
# The keys of /api/status worth showing a model (processes_stats etc. are huge).
STATUS_KEYS = ('version', 'dockerized', 'wrol_mode', 'flags', 'downloads', 'cpu_stats', 'load_stats',
               'memory_stats', 'drives_stats', 'local_time', 'update_available', 'git_branch')


def _limit(limit) -> int:
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _offset(offset) -> int:
    try:
        return max(int(offset), 0)
    except (TypeError, ValueError):
        return 0


def _clean(body: dict) -> dict:
    return {k: v for k, v in body.items() if v not in (None, [], '')}


async def _get_or_none(path: str, params: dict | None = None):
    """GET, returning None when the item does not exist (WROLPi answers 404, or 400 for some models)."""
    try:
        return await api_get(path, params=params)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return None
        raise


# ---------------------------------------------------------------------------
# Library tools
# ---------------------------------------------------------------------------

async def _resolve_channel(channel: str):
    """A channel id or (partial) name -> (channel_id, error_text)."""
    if str(channel).isdigit():
        return int(channel), None
    data = await api_post('/api/collections/search', json=dict(kind='channel', search_str=channel))
    matches = data.get('collections') or []
    exact = [i for i in matches if (i.get('name') or '').lower() == channel.lower()]
    if len(exact) == 1:
        return exact[0]['id'], None
    if len(matches) == 1:
        return matches[0]['id'], None
    if not matches:
        return None, f'No channel matches "{channel}". Use list_collections(kind="channel") to see the channels.'
    names = ', '.join(f'{i.get("name")} (ID: {i.get("id")})' for i in matches[:20])
    return None, f'Several channels match "{channel}"; pass one of their IDs as channel: {names}'


@mcp.tool(annotations=READ_ONLY)
async def search_files(
    query: str | None = None,
    kind: str | None = None,
    channel: str | None = None,
    domain: str | None = None,
    author: str | None = None,
    subject: str | None = None,
    tag_names: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    deep: bool = False,
) -> str:
    """Search the whole library (videos, archived web pages, documents/ebooks) in one call.

    Args:
        query: Text to search for in titles (and, with deep=True, captions/page text).  Omit to browse
            the newest items.
        kind: Narrow to "video", "archive", or "doc".
        channel: A video channel name (or id); implies kind=video.
        domain: An archived site's domain, e.g. "example.com"; implies kind=archive.
        author: Document author (partial match); implies kind=doc.
        subject: Document subject (partial match); implies kind=doc.
        tag_names: Only items with these tags.
        limit: Maximum results (at most 100).
        offset: Pagination offset.
        deep: Also search inside captions, page text and descriptions.  Slower on a Raspberry Pi; use
            it when a title search finds nothing.
    """
    limit, offset = _limit(limit), _offset(offset)
    if channel:
        kind = 'video'
    elif domain:
        kind = 'archive'
    elif author or subject:
        kind = 'doc'

    common = dict(search_str=query, tag_names=tag_names, limit=limit, offset=offset, deep=deep)
    if kind == 'video':
        body = dict(common, headline=True)
        if channel:
            channel_id, error = await _resolve_channel(channel)
            if error:
                return error
            body['channel_id'] = channel_id
        data = await api_post('/api/videos/search', json=_clean(body))
    elif kind == 'archive':
        data = await api_post('/api/archive/search', json=_clean(dict(common, domain=domain, headline=True)))
    elif kind == 'doc':
        data = await api_post('/api/docs/search', json=_clean(dict(common, author=author, subject=subject)))
    elif kind:
        return 'Unknown kind; use "video", "archive", or "doc", or omit it to search everything.'
    else:
        data = await api_post('/api/files/search', json=_clean(dict(common, headline=True)))

    file_groups = data.get('file_groups') or []
    total = (data.get('totals') or {}).get('file_groups')
    return render.render_file_groups(file_groups, total, searched=bool(query))


async def _get_detail(file_group_id: int, kind: str | None = None) -> tuple[str | None, dict | None, dict]:
    """Fetch the detail of one item, trying video, archive, then doc.  Returns (kind, file_group, data)."""
    routes = {
        'video': f'/api/videos/{file_group_id}',
        'archive': f'/api/archive/{file_group_id}',
        'doc': f'/api/docs/{file_group_id}',
    }
    kinds = [kind] if kind in routes else list(routes)
    for k in kinds:
        data = await _get_or_none(routes[k], params={'skip_viewed': 'true'})
        if data and data.get('file_group'):
            return k, data['file_group'], data
    return None, None, {}


@mcp.tool(annotations=READ_ONLY)
async def get_file(file_group_id: int, kind: str | None = None) -> str:
    """Get details about one item of any kind (video, archived page, document) by its ID from search results.

    Args:
        file_group_id: The ID from search results.
        kind: "video", "archive", or "doc" if known (skips guessing).
    """
    found_kind, fg, data = await _get_detail(file_group_id, kind)
    if not fg:
        return f'No video, archived page, or document has ID {file_group_id}. Use search_files to find IDs.'
    item = render.format_file_group(fg, description_length=render.DETAIL_DESCRIPTION_LENGTH, detail=True,
                                    include_url=found_kind == 'archive', doc=data.get('doc'))
    if found_kind == 'archive' and data.get('history'):
        item['history'] = data['history']
    return render.render_item(item)


@mcp.tool(annotations=READ_ONLY)
async def read_content(file_group_id: int, part: str = 'text', offset: int = 0) -> str:
    """Read what an item says: a video's captions or an archived page's text (part="text"), or a video's
    comments (part="comments").  Long content is returned in windows; continue with the offset the
    result gives you.

    Args:
        file_group_id: The ID from search results.
        part: "text" (default) or "comments".
        offset: Character offset to continue reading from.
    """
    if part not in ('text', 'comments'):
        return 'part must be "text" or "comments".'

    if part == 'comments':
        data = await _get_or_none(f'/api/videos/{file_group_id}/comments')
        text = render.format_comments((data or {}).get('comments'))
        return page_text(text, offset) if text else f'No comments available for item {file_group_id}.'

    captions = await _get_or_none(f'/api/videos/{file_group_id}/captions')
    if captions is not None:
        text = render.format_caption_chunks(captions.get('captions'))
        if text:
            return page_text(text, offset)
        return f'Video {file_group_id} has no captions. Try get_file for its details, or part="comments".'

    archive = await _get_or_none(f'/api/archive/{file_group_id}')
    if archive and archive.get('file_group'):
        fg = archive['file_group']
        text = await _read_archive_text(fg)
        if text:
            return page_text(text, offset)
        return f'Archived page {file_group_id} has no readable text.'

    return (f'No text available for item {file_group_id}: only videos with captions and archived pages can be'
            f' read. Use get_file for its details and LINK.')


async def _read_archive_text(fg: dict) -> str | None:
    """The readable text of an archive: the readability text file, else the singlefile HTML stripped."""
    directory = (fg.get('directory') or '').rstrip('/')
    data = fg.get('data') or {}

    def path_of(name):
        if not name:
            return None
        return f'{directory}/{name}' if directory and '/' not in name else name

    if path := path_of(data.get('readability_txt_path')):
        try:
            return await api_get_text(render.media_url(path))
        except httpx.HTTPStatusError:
            pass
    if path := path_of(data.get('singlefile_path')) or fg.get('primary_path'):
        try:
            return render.html_to_text(await api_get_text(render.media_url(path)))
        except httpx.HTTPStatusError:
            pass
    return None


# ---------------------------------------------------------------------------
# Zim tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
async def search_zim(
    query: str,
    zim_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Search the Zim encyclopedias (Wikipedia, Wiktionary, etc.).

    Without zim_id this searches the Zims that have "search by default" enabled; pass a zim_id
    (from list_zim_files) to search one specific Zim.  Read a result with get_zim_entry.

    Args:
        query: Text to search for.
        zim_id: ID of one Zim file to search; omit to search the default Zims.
        limit: Maximum results (per Zim when searching several).
        offset: Pagination offset.
    """
    body = dict(search_str=query, limit=_limit(limit), offset=_offset(offset))
    try:
        if zim_id is not None:
            data = await api_post(f'/api/zim/search/{zim_id}', json=body)
            zim = data.get('zim') or {}
            zim.setdefault('id', zim_id)
            results = [zim]
        else:
            data = await api_post('/api/zim/search', json=body)
            results = data.get('zims') or []
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f'Zim ID {zim_id} not found. Use list_zim_files to find valid Zim IDs.'
        raise
    return render.render_zim_search(results)


@mcp.tool(annotations=READ_ONLY)
async def get_zim_entry(zim_id: int, entry_path: str, offset: int = 0) -> str:
    """Read a specific article/entry from a Zim file (e.g. a Wikipedia article) as plain text.

    Use search_zim first to find entry paths.

    Args:
        zim_id: ID of the Zim file.
        entry_path: Path to the entry within the Zim file (from search results).
        offset: Character offset to continue reading from.
    """
    link = render.zim_entry_link(zim_id, entry_path)
    try:
        html = await api_get_text(link)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f'Zim ID {zim_id} has no entry "{entry_path}". Use search_zim to find entry paths.'
        raise
    text = render.html_to_text(html)
    return f'LINK: {absolute_link(link)}\n\n{page_text(text, offset)}'


@mcp.tool(annotations=READ_ONLY)
async def list_zim_files() -> str:
    """List all available Zim encyclopedias (Wikipedia, Wiktionary, etc.).

    Returns Zim IDs needed for search_zim and get_zim_entry.
    """
    data = await api_get('/api/zim/')
    zims = data.get('zims') or []
    if not zims:
        return 'No Zim files found.'
    lines = []
    for zim in zims:
        metadata = zim.get('metadata') or {}
        parts = [
            f'ID: {zim.get("id")} | {metadata.get("title") or zim.get("path") or "Unknown"}',
            f'  Creator: {metadata.get("creator") or "Unknown"}',
            f'  Description: {metadata.get("description") or "N/A"}',
            f'  Size: {render.format_size(zim.get("size"))}',
            f'  Searched by default: {"yes" if zim.get("auto_search") else "no"}',
        ]
        lines.append('\n'.join(parts))
    return '\n\n'.join(lines)


# ---------------------------------------------------------------------------
# Browsing / listing tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
async def list_collections(
    kind: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """List collections (video channels, archived domains, playlists, document authors and subjects)
    in the library, paged.

    Args:
        kind: Filter by collection kind: "channel", "domain", "playlist", "author", "subject", or None for all.
        query: Match collection names containing this text.
        limit: Maximum results.
        offset: Pagination offset (from a previous listing's "next offset").
    """
    limit, offset = _limit(limit), _offset(offset)
    data = await api_post('/api/collections/search', json=_clean(dict(kind=kind, search_str=query)))
    collections = data.get('collections') or []
    total = len(collections)
    collections = collections[offset:offset + limit]
    if not collections:
        return 'No collections found.'
    lines = []
    for collection in collections:
        parts = [f'ID: {collection.get("id")} | {collection.get("name") or "Unnamed"} ({collection.get("kind") or "?"})']
        if collection.get('directory'):
            parts.append(f'  Directory: {collection["directory"]}')
        if collection.get('item_count'):
            parts.append(f'  Items: {collection["item_count"]}')
        if collection.get('description'):
            parts.append(f'  Description: {render.truncate(collection["description"], 200)}')
        lines.append('\n'.join(parts))
    lines.append(f'\nTotal: {total}')
    if offset + limit < total:
        lines.append(f'More available: call list_collections again with offset={offset + limit}')
    return '\n\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def list_files(path: str = '', offset: int = 0) -> str:
    """List the directories and files inside one directory of the WROLPi media directory.

    Use this to explore how the library is organized on disk (e.g. "videos/", "archive/", a channel's
    directory).  Directories are listed first, then files.  Every entry's relative path can be passed
    back to list_files to descend.  Large directories are paged; call again with the returned offset.

    Args:
        path: Directory relative to the media directory (e.g. "videos/SomeChannel").  Empty for the top level.
        offset: Number of entries to skip (from a previous listing's "next offset").
    """
    path = path.strip('/')
    offset = _offset(offset)
    try:
        data = await api_post('/api/files/', json=dict(directories=[path] if path else []))
    except httpx.HTTPStatusError as e:
        # WROLPi answers 500 for a directory that does not exist.
        if e.response.status_code in (400, 404, 500):
            return f'No such directory: {path or "/"}'
        raise

    # The response is the whole tree down to the requested directory; walk to it.
    tree = data.get('files') or {}
    for name in [i for i in path.split('/') if i]:
        node = tree.get(f'{name}/')
        if not isinstance(node, dict):
            return f'No such directory: {path}'
        tree = node.get('children') or {}

    directories = sorted(k for k in tree if k.endswith('/'))
    files = sorted(k for k in tree if not k.endswith('/'))
    entries = [('dir', k) for k in directories] + [('file', k) for k in files]
    total = len(entries)
    window = entries[offset:offset + LIST_PAGE_SIZE]

    lines = [f'Directory: {path or "/"}']
    for kind, name in window:
        node = tree[name]
        if kind == 'dir':
            lines.append(f'  [dir]  {node.get("path") or name}')
        else:
            size = render.format_size(node.get('size'))
            lines.append(f'  [file] {node.get("path") or name}  ({node.get("mimetype") or "unknown"}, {size})')
    if not window:
        lines.append('  (empty)')
    lines.append(f'\nTotal entries: {total}')
    if offset + LIST_PAGE_SIZE < total:
        lines.append(f'More entries available: call list_files again with offset={offset + LIST_PAGE_SIZE}')
    return '\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def read_file(path: str, offset: int = 0) -> str:
    """Read a plain-text file from the media directory by its relative path (from list_files).

    Args:
        path: File path relative to the media directory, e.g. "notes/todo.txt".  Text files only.
        offset: Character offset to continue reading from.
    """
    path = path.strip('/')
    try:
        info = (await api_post('/api/files/file', json=dict(file=path, skip_tracking=True))).get('file') or {}
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (400, 404):
            return f'Could not read {path}: no such file'
        raise
    mimetype = info.get('mimetype') or ''
    if not (mimetype.startswith('text/') or mimetype in ('application/json', 'application/xml',
                                                          'application/x-yaml', 'application/yaml')):
        return f'Could not read {path}: not a text file ({mimetype or "unknown type"})'
    if (info.get('size') or 0) > MAX_TEXT_FILE_SIZE:
        return f'Could not read {path}: the file is too large ({render.format_size(info.get("size"))})'
    text = await api_get_text(render.media_url(path))
    return page_text(text, offset) if text else f'{path} is empty.'


@mcp.tool(annotations=READ_ONLY)
async def list_tags() -> str:
    """List every tag in the library with how many files, Zim entries, channels, and domains carry it.

    Tag names can be passed as tag_names to the search tools.  Users tag what they care about, so this
    is a good map of their interests.  Also returns the most recently used tag names.
    """
    data = await api_get('/api/tag')
    tags = data.get('tags') or []
    if not tags:
        return 'No tags found.'
    lines = []
    for tag in tags:
        counts = ', '.join(f'{tag.get(key, 0)} {label}' for key, label in (
            ('file_group_count', 'files'), ('zim_entry_count', 'zim entries'),
            ('channel_count', 'channels'), ('domain_count', 'domains')) if tag.get(key))
        lines.append(f'  {tag.get("name")}' + (f'  ({counts})' if counts else '  (unused)'))
    text = f'Tags ({len(tags)}):\n' + '\n'.join(lines)
    try:
        recent = (await api_get('/api/tag/recent')).get('tag_names') or []
    except httpx.HTTPError:
        recent = []
    if recent:
        text += f'\n\nRecently used: {", ".join(recent)}'
    return text


@mcp.tool(annotations=READ_ONLY)
async def list_downloads(status: str | None = None, limit: int = DEFAULT_LIMIT) -> str:
    """Read the WROLPi download queue: summary, recurring downloads (channels, feeds), and one-time downloads.

    Use this when the user asks what is downloading, what failed and why, or what is scheduled.
    Downloads cannot be started, stopped, or retried from here.

    Args:
        status: Only show downloads with this status: new, pending, failed, deferred, or complete.
        limit: Maximum recurring and one-time downloads to show (each).
    """
    limit = _limit(limit)
    data = await api_get('/api/download')
    try:
        summary = (await api_get('/api/status')).get('downloads') or {}
    except httpx.HTTPError:
        summary = {}
    lines = ['Summary: ' + (', '.join(f'{k}={v}' for k, v in summary.items()) or 'unavailable')]

    def wanted(download: dict) -> bool:
        return not status or download.get('status') == status

    def render_download(download: dict) -> str:
        parts = [f'  [{download.get("status")}] {download.get("url")}  (ID: {download.get("id")},'
                 f' {download.get("downloader")})']
        if download.get('frequency'):
            parts.append(f'      every {download["frequency"]}s, next: {download.get("next_download") or "unscheduled"}')
        if download.get('destination'):
            parts.append(f'      destination: {download["destination"]}')
        if download.get('tag_names'):
            parts.append(f'      tags: {", ".join(download["tag_names"])}')
        if download.get('error'):
            parts.append(f'      error: {render.truncate(str(download["error"]).strip(), render.DOWNLOAD_ERROR_LENGTH)}')
        return '\n'.join(parts)

    recurring = [i for i in data.get('recurring_downloads') or [] if wanted(i)][:limit]
    once = [i for i in data.get('once_downloads') or [] if wanted(i)][:limit]
    lines.append(f'\nRecurring downloads ({len(recurring)} shown):')
    lines.extend(render_download(i) for i in recurring) if recurring else lines.append('  none')
    lines.append(f'\nOne-time downloads ({len(once)} shown, {data.get("pending_once_downloads", 0)} pending):')
    lines.extend(render_download(i) for i in once) if once else lines.append('  none')
    return '\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def get_map_overview() -> str:
    """Describe the maps on this WROLPi: downloaded map regions, whether each has a place-search index,
    subscribed regions, and the user's saved pins (with links)."""
    files = (await api_get('/api/map/files')).get('files') or []
    lines = ['Map files:']
    for file in files:
        index = 'searchable' if file.get('has_search_index') else 'no search index'
        lines.append(f'  {file.get("name") or file.get("path")}  ({render.format_size(file.get("size"))}, {index})')
    if not files:
        lines.append('  none')

    try:
        subscriptions = (await api_get('/api/map/subscribe')).get('subscriptions') or []
    except httpx.HTTPError:
        subscriptions = []
    names = [(i.get('name') or i.get('region') or str(i)) if isinstance(i, dict) else str(i) for i in subscriptions]
    lines.append(f'\nSubscribed regions: {", ".join(names) if names else "none"}')

    try:
        pins = (await api_get('/api/map/pins')).get('pins') or []
    except httpx.HTTPError:
        pins = []
    lines.append('\nPins:')
    for pin in pins:
        lines.append(f'  {pin.get("label") or "Unlabeled"}  ({pin.get("lat")}, {pin.get("lon")})'
                     f'  LINK: {absolute_link(render.map_link(pin.get("lat"), pin.get("lon")))}')
    if not pins:
        lines.append('  none')
    return '\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def search_places(
    query: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Find towns, cities, and landmarks by name in the downloaded maps.  Each result links to the WROLPi map.

    Args:
        query: Place name (prefix match), e.g. "Portland".
        limit: Maximum results.
        offset: Pagination offset.
        lat: Latitude to rank nearby places first.
        lon: Longitude to rank nearby places first.
    """
    params = dict(q=query, limit=_limit(limit), offset=_offset(offset))
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)
    data = await api_get('/api/map/search', params=params)
    results = data.get('results') or []
    if not results:
        return 'No places found. The maps may have no search index (see get_map_overview).'
    lines = []
    for i, place in enumerate(results, 1):
        detail = ', '.join(str(place[k]) for k in ('kind', 'kind_detail', 'region') if place.get(k))
        if place.get('population'):
            detail += f', pop. {place["population"]:,}'
        lines.append(f'{i}. {place.get("name")}  ({detail})  at {place.get("lat")}, {place.get("lon")}'
                     f'  LINK: {absolute_link(render.map_link(place.get("lat"), place.get("lon")))}')
    lines.append(f'\nTotal matching: {data.get("total", len(results))}')
    return '\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def get_statistics() -> str:
    """Get an overview of what content is stored in the WROLPi library.

    Returns counts and sizes for videos, archives, ebooks, Zim files, and other content.
    """
    data = await api_get('/api/statistics')
    return json.dumps(data, indent=2, default=str)


@mcp.tool(annotations=READ_ONLY)
async def get_inventory(inventory_slug: str | None = None) -> str:
    """List the inventories (emergency supplies, food storage, etc.), or read one in full.

    Args:
        inventory_slug: The slug of one inventory to read with every item; omit to list them all.
    """
    inventories = (await api_get('/api/inventory/')).get('inventories') or []
    if inventory_slug:
        for inventory in inventories:
            if inventory.get('slug') == inventory_slug:
                return json.dumps(inventory, indent=2, default=str)
        return f'No inventory has slug "{inventory_slug}". Call get_inventory without a slug to list them.'
    if not inventories:
        return 'No inventories found.'
    lines = []
    for inventory in inventories:
        parts = [f'Slug: {inventory.get("slug")} | {inventory.get("name") or "Unnamed"}']
        if inventory.get('type'):
            parts.append(f'  Type: {inventory["type"]}')
        parts.append(f'  Items: {len(inventory.get("items") or [])}')
        lines.append('\n'.join(parts))
    lines.append(f'\nLINK: {absolute_link("/inventory")}')
    return '\n\n'.join(lines)


@mcp.tool(annotations=READ_ONLY)
async def get_status() -> str:
    """Get WROLPi system status (version, mode, download summary, CPU, memory, disks, flags)."""
    data = await api_get('/api/status')
    lean = {k: data[k] for k in STATUS_KEYS if k in data}
    if isinstance(lean.get('version'), str):
        lean['version'] = lean['version'].strip()
    return json.dumps(lean, indent=2, default=str)


def main():
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
