"""Core logic: fetch proxy-list sources, validate proxies, no external deps."""
import json
import logging
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# ---- configuration (no magic numbers buried in the logic) ----

DEFAULT_TIMEOUT = 8          # seconds per request; free proxies are slow
DEFAULT_MAX_WORKERS = 50     # cap on concurrent network operations
DEFAULT_DOMAINS_FILE = 'domains.txt'
DEFAULT_OUTPUT_FILE = 'proxies_checked.txt'

# Many proxy-list sites reject the default 'Python-urllib' agent with 403,
# so we present a common browser User-Agent.
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0 Safari/537.36')

# Validation endpoints. Each must echo an arbitrary path segment (our nonce)
# back in its JSON body — this is what makes the check un-spoofable — and
# report the caller's IP as JSON "origin" for the anonymity comparison.
# {nonce} is substituted per check. Several hosts guard against one being down.
CHECK_ENDPOINTS = [
    'https://httpbingo.org/anything/{nonce}',
    'https://httpbin.org/anything/{nonce}',
]


@dataclass
class Proxy:
    """A proxy plus whatever the validation step could learn about it."""
    ip: str
    port: str
    latency: float = None       # seconds for the validating request
    anonymous: bool = None      # True if it hid our real IP from the endpoint
    checked_at: str = None      # ISO-8601 UTC timestamp of the check
    exit_ip: str = None         # IP the destination saw (the proxy's exit)
    anonymity: str = None       # 'anonymous' | 'transparent' | None

    @property
    def address(self) -> str:
        return f'{self.ip}:{self.port}'

    def __str__(self) -> str:
        return self.address


class _ProxyTableParser(HTMLParser):
    """Collect table rows as lists of <td> cell texts (stdlib, no deps)."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._row = []
        elif tag == 'td' and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self._cell is not None:
            self._row.append(''.join(self._cell).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _read_url_lines(path: str) -> list:
    """Read a URL-per-line file, skipping blanks and '#' comments."""
    with open(path) as f:
        return [line.strip() for line in f
                if line.strip() and not line.lstrip().startswith('#')]


def parse_table_proxies(html_text: str) -> list:
    """
    Parse Proxy objects from any standard proxy-list HTML table.
    First cell is the IP, second the port; the digits-only port check
    drops header rows and unrelated tables on the page.
    :param html_text: raw HTML of a proxy-list page
    :return: list of Proxy
    """
    parser = _ProxyTableParser()
    parser.feed(html_text)
    proxies = []
    for row in parser.rows:
        if len(row) >= 2 and row[0] and row[1].isdigit():
            proxies.append(Proxy(row[0], row[1]))
    return proxies


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT, proxy: str = None):
    """
    Minimal stdlib HTTP GET (no external deps).
    :param url: URL to fetch
    :param timeout: socket timeout in seconds
    :param proxy: optional 'ip:port' to route the request through
    :return: (status_code, response_text)
    """
    if proxy:
        handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode('utf-8', 'replace')


class Source:
    """A proxy-list source: a URL plus the parser that understands it.

    The default parser handles the common HTML table layout; pass a custom
    ``parser`` (e.g. a JSON-API parser) to support a differently shaped source
    without touching the core.
    """

    def __init__(self, url: str, parser=parse_table_proxies):
        self.url = url
        self.parser = parser

    def fetch(self, timeout: int = DEFAULT_TIMEOUT) -> list:
        try:
            status, text = http_get(self.url, timeout=timeout)
        except Exception as e:
            logger.warning('fetch failed for %s: %s', self.url, e)
            return []
        if status != 200:
            logger.warning('fetch %s returned status %s', self.url, status)
            return []
        return self.parser(text)


def _origin_ip(nonce: str, text: str):
    """Return the reported origin IP iff the body genuinely echoes our nonce.

    A proxy that merely replays a canned response cannot know the random
    nonce, so a missing nonce means the request was not actually relayed.
    """
    if nonce not in text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    origin = data.get('origin')
    return origin.split(',')[0].strip() if isinstance(origin, str) else None


def get_own_ip(timeout: int = DEFAULT_TIMEOUT):
    """Fetch this host's public IP directly, for the anonymity comparison."""
    nonce = uuid.uuid4().hex
    for template in CHECK_ENDPOINTS:
        try:
            _, text = http_get(template.format(nonce=nonce), timeout=timeout)
        except Exception:
            continue
        ip = _origin_ip(nonce, text)
        if ip:
            return ip
    logger.warning('could not determine own IP; anonymity will be unknown')
    return None


def check_proxy(proxy, timeout: int = DEFAULT_TIMEOUT, own_ip: str = None):
    """
    Validate one proxy against CHECK_ENDPOINTS using an anti-spoofing nonce.
    :param proxy: a Proxy or an 'ip:port' string
    :param timeout: per-request timeout in seconds
    :param own_ip: this host's public IP; if given, sets Proxy.anonymous
    :return: the enriched Proxy if valid, else None
    """
    if not isinstance(proxy, Proxy):
        text = str(proxy)
        if ':' not in text:
            return None
        ip, _, port = text.partition(':')
        proxy = Proxy(ip, port)
    if not proxy.ip or not proxy.port:
        return None

    nonce = uuid.uuid4().hex
    for template in CHECK_ENDPOINTS:
        url = template.format(nonce=nonce)
        start = time.monotonic()
        try:
            _, body = http_get(url, timeout=timeout, proxy=proxy.address)
        except Exception:
            continue  # endpoint unreachable via this proxy, try the next
        origin = _origin_ip(nonce, body)
        if origin is None:
            continue  # nonce not echoed -> not a genuine relay
        proxy.latency = round(time.monotonic() - start, 3)
        proxy.checked_at = datetime.now(timezone.utc).isoformat(
            timespec='seconds')
        proxy.exit_ip = origin
        if own_ip is not None:
            # our real IP appearing anywhere (origin, X-Forwarded-For, Via,
            # ...) means the proxy leaked it -> transparent, else anonymous.
            leaked = own_ip in body
            proxy.anonymous = not leaked
            proxy.anonymity = 'transparent' if leaked else 'anonymous'
        return proxy
    return None


class FreeProxies:
    """Orchestrates fetching, validating and saving valid proxies."""

    def __init__(self, sources, timeout: int = DEFAULT_TIMEOUT,
                 max_workers: int = DEFAULT_MAX_WORKERS):
        self.sources = list(sources)
        self.timeout = timeout
        self.max_workers = max_workers
        self.proxies = []   # raw, collected
        self.valid = []     # confirmed good

    @classmethod
    def from_file(cls, path: str = DEFAULT_DOMAINS_FILE, **kwargs):
        """Build from a file of source URLs, one per line (I/O stays here)."""
        urls = _read_url_lines(path)
        return cls([Source(u) for u in urls], **kwargs)

    def _workers_for(self, count: int) -> int:
        return max(1, min(count, self.max_workers))

    def collect(self) -> list:
        """Fetch every source concurrently and dedupe the raw proxies."""
        if not self.sources:
            self.proxies = []
            return self.proxies
        seen, result = set(), []
        with ThreadPoolExecutor(self._workers_for(len(self.sources))) as pool:
            futures = [pool.submit(s.fetch, self.timeout)
                       for s in self.sources]
            for future in as_completed(futures):
                for proxy in future.result():
                    if proxy.address not in seen:
                        seen.add(proxy.address)
                        result.append(proxy)
        self.proxies = result
        logger.info('collected %d unique proxies', len(result))
        return result

    def validate(self, proxies=None, on_valid=None) -> list:
        """
        Validate proxies concurrently. ``on_valid(proxy)`` is called for each
        good proxy the moment it is confirmed (streaming).
        :return: the list of valid proxies
        """
        proxies = self.proxies if proxies is None else list(proxies)
        if not proxies:
            self.valid = []
            return self.valid
        own_ip = get_own_ip(self.timeout)
        valid = []
        with ThreadPoolExecutor(self._workers_for(len(proxies))) as pool:
            futures = [pool.submit(check_proxy, p, self.timeout, own_ip)
                       for p in proxies]
            for future in as_completed(futures):
                proxy = future.result()
                if proxy is not None:
                    valid.append(proxy)
                    if on_valid:
                        on_valid(proxy)
        self.valid = valid
        logger.info('validated %d/%d proxies good', len(valid), len(proxies))
        return valid

    def harvest(self, out_file: str = None) -> list:
        """collect() then validate(), streaming valid addresses to out_file."""
        self.collect()
        sink = open(out_file, 'w') if out_file else None

        def _write(proxy):
            if sink:
                sink.write(proxy.address + '\n')
                sink.flush()

        try:
            self.validate(on_valid=_write)
        finally:
            if sink:
                sink.close()
        return self.valid
