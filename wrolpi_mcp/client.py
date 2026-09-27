"""HTTP client for the WROLPi API and the /media file server."""
import httpx

from wrolpi_mcp.config import API_BASE_URL, TIMEOUT, VERIFY_TLS


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE_URL, verify=VERIFY_TLS, timeout=TIMEOUT, follow_redirects=True)


async def api_get(path: str, params: dict | None = None) -> dict:
    """GET a WROLPi API endpoint, returning parsed JSON."""
    async with _client() as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def api_post(path: str, json: dict | None = None) -> dict:
    """POST to a WROLPi API endpoint, returning parsed JSON."""
    async with _client() as client:
        resp = await client.post(path, json=json or {})
        resp.raise_for_status()
        return resp.json()


async def api_get_text(path: str, params: dict | None = None) -> str:
    """GET a path (API or /media) that returns text or HTML."""
    async with _client() as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.text
