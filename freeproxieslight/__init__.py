"""freeproxieslight — a lightweight, dependency-free valid-proxy harvester."""
from . import socks
from .core import (
    CHECK_ENDPOINTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUTPUT_FILE,
    DEFAULT_TIMEOUT,
    FreeProxies,
    Proxy,
    Source,
    check_proxy,
    get_own_ip,
    http_get,
    parse_socks_table,
    parse_table_proxies,
)

__version__ = '2.1.0'

__all__ = [
    'FreeProxies',
    'Proxy',
    'Source',
    'check_proxy',
    'get_own_ip',
    'http_get',
    'parse_table_proxies',
    'parse_socks_table',
    'socks',
    'CHECK_ENDPOINTS',
    'DEFAULT_TIMEOUT',
    'DEFAULT_MAX_WORKERS',
    'DEFAULT_OUTPUT_FILE',
    '__version__',
]
