# WROLPi MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM (Claude Desktop, Claude Code, or
any MCP client) search and read the contents of your [WROLPi](https://wrolpi.org): videos and their
captions, archived web pages, ebooks and documents, Zim encyclopedias, maps, tags, inventories, and
the download queue.

The server is read-only. It runs on your computer, not on the WROLPi, and talks to the WROLPi's
normal HTTP API over your network. Every result carries a link back into the WROLPi web app.

## Requirements

* A WROLPi reachable from this computer (for example `https://wrolpi.local:8443`).
* [uv](https://docs.astral.sh/uv/) (recommended) or Python 3.11+ with `pipx`.

## Install

The quickest way is to let `uvx` fetch and run it straight from this repository:

```bash
uvx --from git+https://github.com/wrolpi/mcp wrolpi-mcp
```

Or install it as a persistent command:

```bash
pipx install git+https://github.com/wrolpi/mcp
# or
uv tool install git+https://github.com/wrolpi/mcp
```

## Configure

The server reads these environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `WROLPI_API_URL` | `https://localhost:8443` | The WROLPi's address, exactly as you type it in a browser. |
| `WROLPI_VERIFY_TLS` | `false` | Verify the TLS certificate. WROLPi uses a self-signed one by default. |
| `WROLPI_TIMEOUT` | `300` | Seconds to wait for a response. Deep searches and searching every Zim at once can take minutes on a Raspberry Pi. |

### Claude Code

```bash
claude mcp add wrolpi -e WROLPI_API_URL=https://wrolpi.local:8443 -- \
    uvx --from git+https://github.com/wrolpi/mcp wrolpi-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json` (Settings > Developer > Edit Config):

```json
{
  "mcpServers": {
    "wrolpi": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wrolpi/mcp", "wrolpi-mcp"],
      "env": {"WROLPI_API_URL": "https://wrolpi.local:8443"}
    }
  }
}
```

If you installed it with `pipx` or `uv tool`, use `"command": "wrolpi-mcp"` with no `args`.

### LM Studio

LM Studio 0.3.17 or newer can run MCP servers. Open a chat, switch to the **Program** tab in the
right-hand sidebar, click **Install > Edit mcp.json**, and add the `wrolpi` entry (the file is
`~/.lmstudio/mcp.json`, or `%USERPROFILE%\.lmstudio\mcp.json` on Windows):

```json
{
  "mcpServers": {
    "wrolpi": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wrolpi/mcp", "wrolpi-mcp"],
      "env": {"WROLPI_API_URL": "https://wrolpi.local:8443"}
    }
  }
}
```

Save the file, then turn the `wrolpi` server on in the Program tab for the chat. The first tool call
opens a confirmation dialog where you can allow it once or always.

Notes:

* Use a model that supports tool calling (LM Studio marks them with a tool icon). Small models
  can emit tool calls but get unreliable as the tool list grows.
* LM Studio is a GUI app and may not see the `PATH` of your shell. If the server fails to start with
  a "command not found" error, put the full path to `uvx` in `command` (`which uvx` on macOS/Linux,
  `where uvx` on Windows), or install it with `pipx`/`uv tool` and use the full path to `wrolpi-mcp`.

## Tools

| Tool | What it does |
|---|---|
| `search_files` | Search videos, archived pages and documents in one call. Filter by kind, channel, domain, author, subject, or tags. `deep=true` also searches captions and page text. |
| `get_file` | Details of one item by its ID. |
| `read_content` | A video's captions or comments, or an archived page's text. |
| `search_zim`, `get_zim_entry`, `list_zim_files` | Search and read Zim encyclopedias (Wikipedia, Wiktionary, ...). |
| `list_collections` | Video channels, archived domains, and playlists. |
| `list_tags` | Every tag with its usage counts. |
| `list_files`, `read_file` | Browse the media directory and read plain-text files. |
| `list_downloads` | The download queue: what is scheduled, running, or failed. |
| `get_map_overview`, `search_places` | Downloaded map regions, pins, and place search. |
| `get_inventory` | Inventories (food storage, supplies) and their items. |
| `get_statistics`, `get_status` | Library statistics and system status. |

## Troubleshooting

**Every tool fails, or a search fails and then everything after it does.** The usual cause is one
slow request. A `deep=True` search or a search across every Zim can take minutes on a Raspberry Pi; if
the server gives up first, the WROLPi keeps working on the abandoned request and the next calls queue
behind it. Since 0.1.1 the model is told exactly this (and how to retry) instead of a bare
"Error executing tool". If it still happens, raise `WROLPI_TIMEOUT`, or ask for title-only searches.

**The model never sees an error message, only "Error executing tool".** Upgrade the server: that is how
older versions reported a timeout. With `uvx`, clear the cached copy so it fetches the latest:

```bash
uv cache clean wrolpi-mcp
```

**"Could not reach the WROLPi".** `WROLPI_API_URL` must be the address you type in a browser,
including `https://` and the port (`https://wrolpi.local:8443`). The server's log goes to the
MCP client's log (LM Studio: **Developer > Logs**; Claude Desktop: `~/Library/Logs/Claude/mcp*.log`).

## Development

```bash
git clone https://github.com/wrolpi/mcp wrolpi-mcp
cd wrolpi-mcp
uv sync
uv run pytest
# Try the tools interactively in the MCP Inspector:
WROLPI_API_URL=https://wrolpi.local:8443 uv run mcp dev wrolpi_mcp/server.py
```

`tests/test_live.py` runs every tool against a real WROLPi when `WROLPI_LIVE_URL` is set:

```bash
WROLPI_LIVE_URL=https://wrolpi.local:8443 uv run pytest tests/test_live.py
```

## License

GPLv3, the same as WROLPi.
