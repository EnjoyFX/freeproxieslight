import json
import os
import socket
import threading
from unittest import TestCase, mock
from urllib.error import HTTPError

from freeproxieslight import (FreeProxies, Proxy, Source, check_proxy,
                              get_own_ip, http_get, parse_socks_table,
                              parse_table_proxies)
from freeproxieslight.cli import main as cli_main
from freeproxieslight.socks import socks_http_get

HERE = os.path.dirname(os.path.abspath(__file__))


def fixture(name):
    """Resolve a fixture path so tests run from any working directory."""
    return os.path.join(HERE, name)


test_file = fixture('test_domains.txt')  # file with test source URL(s)
expected = ['151.106.13.222:1080',
            '195.158.30.232:3128']  # proxies present in page.html

OWN_IP = '203.0.113.9'    # our "real" IP as seen on a direct call
EXIT_IP = '198.51.100.7'  # IP a working proxy exits from (hides ours)


def mocked_http_get(url, timeout=None, proxy=None):
    """Fake http_get covering scraping + nonce/anonymity validation.

    Proxy addresses drive the behaviour:
      1.2.3.4:8080  genuine relay (valid, anonymous)
      5.5.5.5:80    spoofer — canned body, does NOT echo the nonce
      6.6.6.6:80    first endpoint down, second works (fallback)
      7.7.7.7:80    every endpoint unreachable
    """
    if url == 'http://source.example/list':
        with open(fixture('page.html')) as f:
            return 200, f.read()
    if url == 'http://source.example/500':
        return 500, ''
    if '/anything/' not in url:
        raise HTTPError(url, 404, 'Not Found', {}, None)

    # --- validation endpoints ---
    if proxy is None:  # direct call used to learn our own IP
        return 200, json.dumps({'url': url, 'origin': OWN_IP})
    if proxy == '7.7.7.7:80':
        raise Exception('connection refused')
    if proxy == '5.5.5.5:80':  # replays a fixed body, no nonce echo
        return 200, json.dumps(
            {'url': 'https://httpbin.org/anything/canned', 'origin': EXIT_IP})
    if proxy == '6.6.6.6:80' and 'httpbingo.org' in url:
        raise HTTPError(url, 502, 'Bad Gateway', {}, None)
    # genuine relay: echoes the requested url (with the real nonce)
    return 200, json.dumps({'url': url, 'origin': EXIT_IP})


class TestParsing(TestCase):
    def test_parse_page(self):
        with open(fixture('page.html')) as f:
            html = f.read()
        proxies = parse_table_proxies(html)
        self.assertTrue(all(isinstance(p, Proxy) for p in proxies))
        self.assertEqual([p.address for p in proxies], expected)

    def test_parse_ignores_header_and_noise(self):
        html = ('<table><thead><tr><th>IP</th><th>Port</th></tr></thead>'
                '<tbody><tr><td>1.2.3.4</td><td>8080</td></tr></tbody></table>'
                '<table><tr><td>foo</td><td>bar</td></tr></table>')
        self.assertEqual(
            [p.address for p in parse_table_proxies(html)], ['1.2.3.4:8080'])


class TestSource(TestCase):
    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_fetch_parses(self, _m):
        proxies = Source('http://source.example/list').fetch()
        self.assertEqual([p.address for p in proxies], expected)

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_fetch_non_200_returns_empty(self, _m):
        self.assertEqual(Source('http://source.example/500').fetch(), [])

    @mock.patch('freeproxieslight.core.http_get',
                side_effect=Exception('boom'))
    def test_fetch_error_returns_empty(self, _m):
        self.assertEqual(Source('http://x').fetch(), [])


class TestHarvester(TestCase):
    def test_from_file(self):
        h = FreeProxies.from_file(test_file)
        self.assertEqual([s.url for s in h.sources],
                         ['https://www.sslproxies.org/'])

    def test_from_file_empty(self):
        h = FreeProxies.from_file(fixture('empty_domains.txt'))
        self.assertEqual(h.sources, [])

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_collect_dedupes(self, _m):
        h = FreeProxies([Source('http://source.example/list'),
                         Source('http://source.example/list')])
        proxies = h.collect()
        self.assertEqual(sorted(p.address for p in proxies), sorted(expected))

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_validate_streams_and_returns(self, _m):
        streamed = []
        good = FreeProxies([]).validate(
            [Proxy('1.2.3.4', '8080'), Proxy('7.7.7.7', '80')],
            on_valid=streamed.append)
        self.assertEqual([p.address for p in good], ['1.2.3.4:8080'])
        self.assertEqual([p.address for p in streamed], ['1.2.3.4:8080'])

    def test_validate_empty(self):
        self.assertEqual(FreeProxies([]).validate([]), [])

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_harvest_end_to_end(self, _m):
        out = fixture('out_proxies.txt')
        good = FreeProxies([Source('http://source.example/list')]).harvest(
            out_file=out)
        try:
            self.assertEqual(sorted(p.address for p in good), sorted(expected))
            with open(out) as f:
                written = sorted(line.strip() for line in f if line.strip())
            self.assertEqual(written, sorted(expected))
        finally:
            os.remove(out)


class TestCheckProxy(TestCase):
    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_valid_proxy_enriched(self, _m):
        p = check_proxy(Proxy('1.2.3.4', '8080'), own_ip=OWN_IP)
        self.assertIsNotNone(p)
        self.assertEqual(p.address, '1.2.3.4:8080')
        self.assertTrue(p.anonymous)          # our IP not leaked
        self.assertEqual(p.anonymity, 'anonymous')
        self.assertEqual(p.exit_ip, EXIT_IP)
        self.assertIsNotNone(p.latency)
        self.assertIsNotNone(p.checked_at)

    def test_transparent_proxy_flagged(self):
        # genuine relay (echoes nonce) but leaks our real IP -> transparent
        def leaky(url, timeout=None, proxy=None):
            return 200, json.dumps({'url': url, 'origin': OWN_IP,
                                    'headers': {'X-Forwarded-For': OWN_IP}})
        with mock.patch('freeproxieslight.core.http_get', side_effect=leaky):
            p = check_proxy(Proxy('8.8.8.8', '80'), own_ip=OWN_IP)
        self.assertIsNotNone(p)
        self.assertFalse(p.anonymous)
        self.assertEqual(p.anonymity, 'transparent')
        self.assertEqual(p.exit_ip, OWN_IP)

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_spoofed_response_rejected(self, _m):
        # proxy replays a canned body without our nonce -> must be rejected
        self.assertIsNone(check_proxy(Proxy('5.5.5.5', '80')))

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_falls_back_to_second_endpoint(self, _m):
        p = check_proxy(Proxy('6.6.6.6', '80'))
        self.assertIsNotNone(p)
        self.assertEqual(p.address, '6.6.6.6:80')

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_unreachable_proxy_rejected(self, _m):
        self.assertIsNone(check_proxy(Proxy('7.7.7.7', '80')))

    def test_empty_or_malformed_input_rejected(self):
        self.assertIsNone(check_proxy(''))
        self.assertIsNone(check_proxy('garbage-no-colon'))

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_get_own_ip(self, _m):
        self.assertEqual(get_own_ip(), OWN_IP)


class TestCli(TestCase):
    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_cli_writes_txt(self, _m):
        out = fixture('cli_out.txt')
        rc = cli_main(['http://source.example/list', '-o', out, '-q'])
        try:
            self.assertEqual(rc, 0)
            with open(out) as f:
                got = sorted(line.strip() for line in f if line.strip())
            self.assertEqual(got, sorted(expected))
        finally:
            os.remove(out)

    @mock.patch('freeproxieslight.core.http_get', side_effect=mocked_http_get)
    def test_cli_writes_json(self, _m):
        out = fixture('cli_out.json')
        rc = cli_main(
            ['http://source.example/list', '-o', out, '-f', 'json', '-q'])
        try:
            self.assertEqual(rc, 0)
            with open(out) as f:
                data = json.load(f)
            self.assertEqual(sorted(d['address'] for d in data),
                             sorted(expected))
            # enriched fields are present in the JSON output
            self.assertTrue(all('exit_ip' in d and 'anonymity' in d
                                for d in data))
        finally:
            os.remove(out)

    def test_cli_missing_sources_file(self):
        self.assertEqual(cli_main(['-s', fixture('nope.txt'), '-q']), 2)

    def test_socks_flag_selects_socks_parser(self):
        from freeproxieslight import core
        from freeproxieslight.cli import _build_parser, _load_sources
        socks_args = _build_parser().parse_args(['http://x', '--socks'])
        http_args = _build_parser().parse_args(['http://x'])
        self.assertEqual(_load_sources(socks_args)[0].parser,
                         core.parse_socks_table)
        self.assertEqual(_load_sources(http_args)[0].parser,
                         core.parse_table_proxies)


class TestHttpGet(TestCase):
    def test_sends_browser_user_agent(self):
        # Real proxy-list sites 403 the default Python-urllib agent, so
        # http_get must present a browser User-Agent.
        captured = {}

        class FakeResp:
            status = 200

            def read(self):
                return b'ok'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                captured['ua'] = req.get_header('User-agent')
                return FakeResp()

        with mock.patch(
                'freeproxieslight.core.urllib.request.build_opener',
                return_value=FakeOpener()):
            status, text = http_get('http://example.com')
        self.assertEqual((status, text), (200, 'ok'))
        self.assertIn('Mozilla', captured['ua'] or '')


def _recvn(conn, n):
    buf = b''
    while len(buf) < n:
        c = conn.recv(n - len(buf))
        if not c:
            break
        buf += c
    return buf


def _read_until_nul(conn):
    data = b''
    while not data.endswith(b'\x00'):
        c = conn.recv(1)
        if not c:
            break
        data += c
    return data


def _start_fake_socks_http(version, body):
    """A one-shot local server: SOCKS handshake, then serve one HTTP reply."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.settimeout(5)
        try:
            if version == 5:
                _recvn(conn, 3)                 # greeting
                conn.sendall(b'\x05\x00')       # no-auth chosen
                atyp = _recvn(conn, 4)[3]       # ver cmd rsv atyp
                if atyp == 3:
                    _recvn(conn, _recvn(conn, 1)[0])
                elif atyp == 1:
                    _recvn(conn, 4)
                _recvn(conn, 2)                 # dest port
                conn.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
            else:
                head = _recvn(conn, 8)          # ver cmd port ip
                _read_until_nul(conn)           # userid
                if head[4:7] == b'\x00\x00\x00' and head[7] != 0:
                    _read_until_nul(conn)       # SOCKS4a hostname
                conn.sendall(b'\x00\x5a\x00\x00\x00\x00\x00\x00')
            req = b''
            while b'\r\n\r\n' not in req:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                req += chunk
            payload = body.encode()
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '
                         + str(len(payload)).encode() + b'\r\n\r\n' + payload)
        finally:
            conn.close()
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


class TestSocks(TestCase):
    def test_socks5_http_get(self):
        port = _start_fake_socks_http(5, 'hello-socks5')
        addr = f'127.0.0.1:{port}'
        status, text = socks_http_get(f'http://{addr}/anything/x', addr,
                                      version=5, timeout=5)
        self.assertEqual((status, text), (200, 'hello-socks5'))

    def test_socks4_http_get(self):
        port = _start_fake_socks_http(4, 'hello-socks4')
        addr = f'127.0.0.1:{port}'
        status, text = socks_http_get(f'http://{addr}/anything/x', addr,
                                      version=4, timeout=5)
        self.assertEqual((status, text), (200, 'hello-socks4'))

    def test_parse_socks_table_sets_scheme(self):
        html = ('<table><tbody>'
                '<tr><td>1.2.3.4</td><td>1080</td><td>US</td>'
                '<td>United States</td><td>Socks5</td><td>Anonymous</td>'
                '<td>Yes</td><td>x</td></tr>'
                '<tr><td>5.6.7.8</td><td>4145</td><td>RU</td><td>Russia</td>'
                '<td>Socks4</td><td>Anonymous</td><td>No</td><td>x</td></tr>'
                '</tbody></table>')
        proxies = parse_socks_table(html)
        self.assertEqual([(p.address, p.scheme) for p in proxies],
                         [('1.2.3.4:1080', 'socks5'),
                          ('5.6.7.8:4145', 'socks4')])

    def test_check_proxy_routes_socks_through_socks_client(self):
        captured = {}

        def fake_socks(url, proxy, version=5, timeout=None, user_agent=None):
            captured['version'] = version
            return 200, json.dumps({'url': url, 'origin': EXIT_IP})

        with mock.patch('freeproxieslight.core.socks.socks_http_get',
                        side_effect=fake_socks):
            p = check_proxy(Proxy('9.9.9.9', '1080', scheme='socks5'),
                            own_ip=OWN_IP)
        self.assertIsNotNone(p)
        self.assertEqual(captured['version'], 5)
        self.assertEqual(p.exit_ip, EXIT_IP)
        self.assertEqual(p.address, '9.9.9.9:1080')
