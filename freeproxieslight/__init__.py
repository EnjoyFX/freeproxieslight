"""freeproxieslight — a lightweight, dependency-free valid-proxy harvester."""
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
    parse_table_proxies,
)

__version__ = '2.0.0'

__all__ = [
    'FreeProxies',
    'Proxy',
    'Source',
    'check_proxy',
    'get_own_ip',
    'http_get',
    'parse_table_proxies',
    'CHECK_ENDPOINTS',
    'DEFAULT_TIMEOUT',
    'DEFAULT_MAX_WORKERS',
    'DEFAULT_OUTPUT_FILE',
    '__version__',
]
