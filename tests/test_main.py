import json
import os
from unittest import TestCase, mock
from urllib.error import HTTPError

from freeproxieslight import (FreeProxies, Proxy, Source, check_proxy,
                              get_own_ip, parse_table_proxies)
from freeproxieslight.cli import main as cli_main

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
        self.assertTrue(p.anonymous)          # exit IP differs from our own
        self.assertIsNotNone(p.latency)
        self.assertIsNotNone(p.checked_at)

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
            self.assertEqual(sorted(d['address'] if 'address' in d
                                    else f"{d['ip']}:{d['port']}"
                                    for d in data), sorted(expected))
        finally:
            os.remove(out)

    def test_cli_missing_sources_file(self):
        self.assertEqual(cli_main(['-s', fixture('nope.txt'), '-q']), 2)
