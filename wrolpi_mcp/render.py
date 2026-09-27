"""Turn WROLPi API JSON into compact text for a model.

Results are flat and short: an ID, a WROLPi link, and only the fields a model needs to choose
or cite an item.  Links are absolute (prefixed with the WROLPi base URL) so the model can hand
them straight to the user."""
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from wrolpi_mcp.config import API_BASE_URL

# Big content (captions, page text, zim entries, plain files) is returned at most this many
# characters at a time; the reading tools take an offset to continue.
MAX_CONTENT_CHARS = 50_000
# Description length in a detail result; descriptions in listings are shorter.
DETAIL_DESCRIPTION_LENGTH = 1_000
LISTING_DESCRIPTION_LENGTH = 300
# Download errors are long tracebacks; the model only needs the gist.
DOWNLOAD_ERROR_LENGTH = 300
MAP_LINK_ZOOM = 12


def encode_media_path(path: str) -> str:
    """URL-encode a media-relative path one segment at a time (slashes stay)."""
    return '/'.join(quote(i, safe='') for i in str(path).split('/'))


def absolute_link(relative: Optional[str]) -> Optional[str]:
    return f'{API_BASE_URL}{relative}' if relative else None


def media_url(path: str) -> str:
    """The Caddy file-server URL of a media-relative path."""
    return f'/media/{encode_media_path(path)}'


def truncate(text: Optional[str], length: int) -> Optional[str]:
    if not text:
        return None
    return text if len(text) <= length else text[:length] + '…'


def page_text(text: Optional[str], offset: int = 0, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Return one window of big text with an explicit continuation hint (never silent truncation)."""
    text = text or ''
    offset = max(int(offset or 0), 0)
    if offset >= len(text) and offset:
        return f'[No more content; the text is {len(text):,} characters long]'
    window = text[offset:offset + max_chars]
    if offset + max_chars < len(text):
        window += (f'\n\n[Truncated: showing characters {offset:,}-{offset + max_chars:,} of {len(text):,}.'
                   f' Call again with offset={offset + max_chars} to continue.]')
    return window


def file_group_kind(fg: dict) -> str:
    """Categorize a FileGroup JSON dict: video/archive/doc/image/file."""
    mimetype = fg.get('mimetype') or ''
    if fg.get('model') == 'video' or isinstance(fg.get('video'), dict):
        return 'video'
    if fg.get('model') == 'archive' or isinstance(fg.get('archive'), dict):
        return 'archive'
    if fg.get('model') in ('doc', 'ebook') or isinstance(fg.get('doc'), dict):
        return 'doc'
    if mimetype.startswith('image/'):
        return 'image'
    return 'file'


def wrolpi_link(fg: dict) -> Optional[str]:
    """The relative WROLPi UI link of a FileGroup JSON dict.

    Videos, docs and archives deep-link by FileGroup ID; epubs open in the reader; everything
    else is served directly from /media/."""
    fg_id = fg.get('id')
    kind = file_group_kind(fg)
    if kind == 'video' and fg_id:
        return f'/videos/{fg_id}'
    if kind == 'doc' and fg_id:
        return f'/docs/{fg_id}'
    if kind == 'archive' and fg_id:
        return f'/archives/{fg_id}'

    primary_path = fg.get('primary_path')
    if not primary_path:
        return None
    mimetype = fg.get('mimetype') or ''
    if mimetype.startswith('application/epub'):
        return f'/epub/epub.html?url={media_url(primary_path)}'
    return media_url(primary_path)


def format_duration(seconds) -> Optional[str]:
    """Seconds as H:MM:SS (or M:SS)."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


def format_date(value) -> Optional[str]:
    """Only the date part of an ISO datetime string; the time of day is noise."""
    if not value:
        return None
    return str(value)[:10]


def format_file_group(fg: dict, description_length: int = LISTING_DESCRIPTION_LENGTH,
                      include_url: bool = False, detail: bool = False, doc: Optional[dict] = None) -> dict:
    """Flatten a FileGroup JSON dict (plus the optional `video`/`doc` dicts of the detail
    endpoints) into the lean shape the renderers consume.  Empty fields are omitted."""
    kind = file_group_kind(fg)
    result = dict(
        id=fg.get('id'),
        kind=kind,
        title=fg.get('title') or fg.get('name'),
        link=wrolpi_link(fg),
        published=format_date(fg.get('published_datetime')),
        author=fg.get('author'),
        tags=fg.get('tags') or None,
        url=fg.get('url') if include_url else None,
    )
    if detail:
        result['mimetype'] = fg.get('mimetype')
        result['size'] = fg.get('size')
        result['path'] = fg.get('primary_path')

    headline = fg.get('d_headline') or fg.get('b_headline') or fg.get('c_headline')
    if headline:
        result['headline'] = headline

    if kind == 'video':
        result['duration'] = format_duration(fg.get('length'))
        video = fg.get('video')
        if isinstance(video, dict):
            channel = video.get('channel')
            if isinstance(channel, dict):
                result['channel'] = channel.get('name') or channel.get('id')
            elif video.get('channel_id'):
                result['channel_id'] = video['channel_id']
            result['has_captions'] = bool(video.get('caption_files'))
            result['has_comments'] = bool(video.get('have_comments'))
            result['view_count'] = video.get('view_count')

    if kind == 'doc':
        doc = doc or fg.get('doc') or {}
        result['subject'] = doc.get('subject')
        result['language'] = doc.get('language')
        result['pages'] = doc.get('page_count')
        result['description'] = truncate(doc.get('description'), description_length)

    return {k: v for k, v in result.items() if v is not None}


_FIELD_LABELS = (
    ('mimetype', 'Type'),
    ('size', 'Size'),
    ('path', 'Path'),
    ('duration', 'Duration'),
    ('published', 'Published'),
    ('channel', 'Channel'),
    ('has_captions', 'Has captions'),
    ('has_comments', 'Has comments'),
    ('view_count', 'Views'),
    ('author', 'Author'),
    ('subject', 'Subject'),
    ('language', 'Language'),
    ('pages', 'Pages'),
    ('url', 'Source URL'),
    ('headline', 'Headline'),
    ('description', 'Description'),
)

_KIND_HINTS = {
    'video': 'use this for get_file, read_content (captions/comments)',
    'archive': 'use this for get_file, read_content (page text)',
    'doc': 'use this for get_file',
}


def render_item(item: dict) -> str:
    """One lean item as concise text.  The link goes first so the model can't drop it."""
    parts = []
    title = item.get('title')
    link = absolute_link(item.get('link'))
    if title and link:
        parts.append(f'Title: {title}  —  LINK: {link}')
    elif title:
        parts.append(f'Title: {title}')
    elif link:
        parts.append(f'LINK: {link}')

    if item.get('id'):
        hint = _KIND_HINTS.get(item.get('kind'))
        parts.append(f'ID: {item["id"]}' + (f'  ({hint})' if hint else ''))
    for key, label in _FIELD_LABELS:
        value = item.get(key)
        if value not in (None, '', False):
            parts.append(f'{label}: {value}')
    if tags := item.get('tags'):
        parts.append(f'Tags: {", ".join(map(str, tags))}')
    if history := item.get('history'):
        parts.append(f'Earlier snapshots ({len(history)}):')
        for snapshot in history:
            parts.append(f'  - {snapshot.get("title") or "Untitled"} (ID: {snapshot.get("id")},'
                         f' {format_date(snapshot.get("published_datetime")) or "?"})')
    return '\n'.join(parts)


EMPTY_SEARCH_HINT = ('No matches. Names may be spelled differently: try fewer or different words, use the'
                     ' exact channel/website names from list_collections, or omit the query to browse the'
                     ' newest items.')


def render_file_groups(file_groups: list, total: Optional[int], searched: bool = False) -> str:
    if not file_groups:
        return EMPTY_SEARCH_HINT if searched else 'No results found.'
    sections = [f'--- Result {i} ---\n{render_item(format_file_group(fg))}'
                for i, fg in enumerate(file_groups, 1)]
    text = '\n\n'.join(sections)
    if total is not None:
        text += f'\n\nTotal matching: {total}'
    return text


def html_to_text(html: str) -> str:
    """Strip HTML down to plain text."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    text = soup.get_text(separator='\n', strip=True)

    lines = []
    prev_blank = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if not prev_blank:
                lines.append('')
            prev_blank = True
        else:
            lines.append(stripped)
            prev_blank = False
    return '\n'.join(lines)


def format_caption_chunks(chunks: Optional[list]) -> str:
    """Caption chunks ({start_seconds, text}) as timestamped lines."""
    if not chunks:
        return ''
    lines = []
    for chunk in chunks:
        minutes, seconds = divmod(int(chunk.get('start_seconds') or 0), 60)
        hours, minutes = divmod(minutes, 60)
        lines.append(f'[{hours:02d}:{minutes:02d}:{seconds:02d}] {chunk.get("text") or ""}')
    return '\n'.join(lines)


def format_comments(comments: Optional[list]) -> str:
    """yt-dlp comment dicts as "author: text" lines, replies indented."""
    if not comments:
        return ''
    lines = []
    for comment in comments:
        indent = '    ' if comment.get('parent') not in (None, 'root') else ''
        author = comment.get('author') or 'anonymous'
        likes = comment.get('like_count')
        suffix = f'  ({likes} likes)' if likes else ''
        lines.append(f'{indent}{author}: {(comment.get("text") or "").strip()}{suffix}')
    return '\n'.join(lines)


def zim_entry_link(zim_id: int, path: str) -> str:
    return f'/api/zim/{zim_id}/entry/{encode_media_path(path)}'


def render_zim_search(zim_results: list) -> str:
    """Flatten per-Zim search results ({metadata, estimate, search: [...]}) into text."""
    entries = []
    total = 0
    for zim_result in zim_results:
        metadata = zim_result.get('metadata') or {}
        total += zim_result.get('estimate') or 0
        for entry in zim_result.get('search') or []:
            zim_id = entry.get('zim_id') or zim_result.get('id')
            entries.append(dict(zim_id=zim_id, zim=metadata.get('title'), path=entry.get('path'),
                                title=entry.get('title'), headline=entry.get('headline')))
    if not entries:
        return 'No results found.'
    sections = []
    for i, entry in enumerate(entries, 1):
        parts = [f'--- Result {i} ---']
        link = absolute_link(zim_entry_link(entry['zim_id'], entry['path'])) if entry.get('path') else None
        if entry.get('title') and link:
            parts.append(f'Title: {entry["title"]}  —  LINK: {link}')
        elif entry.get('title'):
            parts.append(f'Title: {entry["title"]}')
        if entry.get('zim'):
            parts.append(f'Zim: {entry["zim"]}')
        parts.append(f'Zim ID: {entry["zim_id"]} | Path: {entry.get("path") or ""}')
        if entry.get('headline'):
            parts.append(f'Headline: {entry["headline"]}')
        sections.append('\n'.join(parts))
    return '\n\n'.join(sections) + f'\n\nTotal matching: {total}'


def map_link(lat, lon) -> str:
    return f'/map?lat={lat}&lon={lon}&z={MAP_LINK_ZOOM}'


def format_size(size) -> str:
    size = size or 0
    if size >= 1024 ** 3:
        return f'{size / 1024 ** 3:,.2f} GB'
    if size >= 1024 ** 2:
        return f'{size / 1024 ** 2:,.1f} MB'
    return f'{size:,} B'
