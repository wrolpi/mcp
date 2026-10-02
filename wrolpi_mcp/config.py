"""Configuration for the WROLPi MCP server, all from environment variables."""
import os

# WROLPi base URL (the same address the browser uses).  Override with WROLPI_API_URL.
API_BASE_URL = os.environ.get('WROLPI_API_URL', 'https://localhost:8443').rstrip('/')

# WROLPi serves a self-signed certificate by default, so TLS verification is off unless asked for.
VERIFY_TLS = os.environ.get('WROLPI_VERIFY_TLS', 'false').lower() in ('true', '1', 'yes')

# Seconds to wait for the WROLPi API to answer a request.  Deep searches and all-Zim searches can take
# minutes on a Raspberry Pi, and a client that gives up early leaves the WROLPi busy for the next call.
TIMEOUT = float(os.environ.get('WROLPI_TIMEOUT', '300'))

# Seconds to wait for a TCP connection; a wrong address should fail fast, not after TIMEOUT.
CONNECT_TIMEOUT = 10.0

# Default number of results a search tool returns.
DEFAULT_LIMIT = 10
