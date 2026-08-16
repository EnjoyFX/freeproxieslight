"""Minimal, dependency-free SOCKS4a/SOCKS5 client.

Just enough to open a TCP tunnel through a SOCKS proxy and run a single
HTTP(S) GET over it — used to validate SOCKS proxies without pulling in
PySocks or any other third-party package.
"""
import http.client
import socket
import ssl
import struct
from urllib.parse import urlsplit


class SocksError(Exception):
    """A SOCKS handshake or tunnelling failure."""


def _encode_host(host: str) -> bytes:
    """Encode a hostname for the SOCKS request (punycode for IDNs)."""
    try:
        return host.encode('idna')
    except UnicodeError:
        return host.encode('ascii')


def _recv_exact(sock, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise SocksError('connection closed during SOCKS handshake')
        buf += chunk
    return buf


def _socks5_connect(sock, host: str, port: int) -> None:
    # greeting: version 5, one method offered, "no authentication" (0x00)
    sock.sendall(b'\x05\x01\x00')
    ver, method = _recv_exact(sock, 2)
    if ver != 0x05:
        raise SocksError('not a SOCKS5 proxy')
    if method != 0x00:
        raise SocksError('SOCKS5 proxy requires authentication')
    # CONNECT request with the destination as a domain name (remote DNS)
    host_bytes = _encode_host(host)
    if len(host_bytes) > 255:
        raise SocksError('destination host name too long')
    request = (b'\x05\x01\x00\x03' + bytes([len(host_bytes)]) + host_bytes
               + struct.pack('>H', port))
    sock.sendall(request)
    ver, rep, _rsv, atyp = _recv_exact(sock, 4)
    if rep != 0x00:
        raise SocksError(f'SOCKS5 CONNECT failed (reply code {rep})')
    # drain the bound address so the socket is left at the payload boundary
    if atyp == 0x01:
        _recv_exact(sock, 4)
    elif atyp == 0x03:
        _recv_exact(sock, _recv_exact(sock, 1)[0])
    elif atyp == 0x04:
        _recv_exact(sock, 16)
    else:
        raise SocksError(f'SOCKS5 unknown address type {atyp}')
    _recv_exact(sock, 2)  # bound port


def _socks4_connect(sock, host: str, port: int) -> None:
    try:
        ip = socket.inet_aton(host)
        remote_dns = False
    except OSError:
        ip = b'\x00\x00\x00\x01'  # 0.0.0.x signals SOCKS4a (send hostname)
        remote_dns = True
    request = b'\x04\x01' + struct.pack('>H', port) + ip + b'\x00'
    if remote_dns:
        request += _encode_host(host) + b'\x00'
    sock.sendall(request)
    _null, status = _recv_exact(sock, 2)[:2]
    _recv_exact(sock, 6)  # ignored port + address
    if status != 0x5a:
        raise SocksError(f'SOCKS4 CONNECT failed (status {status:#x})')


def open_tunnel(proxy_host: str, proxy_port: int, dest_host: str,
                dest_port: int, version: int = 5, timeout: float = None):
    """Open a socket to the proxy and CONNECT it to dest_host:dest_port."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        if version == 5:
            _socks5_connect(sock, dest_host, dest_port)
        elif version == 4:
            _socks4_connect(sock, dest_host, dest_port)
        else:
            raise SocksError(f'unsupported SOCKS version {version}')
    except Exception:
        sock.close()
        raise
    return sock


def socks_http_get(url: str, proxy: str, version: int = 5,
                   timeout: float = None, user_agent: str = None):
    """
    HTTP(S) GET routed through a SOCKS proxy, mirroring core.http_get.
    :param url: target URL
    :param proxy: 'ip:port' of the SOCKS proxy
    :param version: 4 or 5
    :param timeout: socket timeout in seconds
    :param user_agent: optional User-Agent header
    :return: (status_code, response_text)
    """
    parts = urlsplit(url)
    https = parts.scheme == 'https'
    host = parts.hostname
    port = parts.port or (443 if https else 80)
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query

    proxy_host, proxy_port = proxy.rsplit(':', 1)
    sock = open_tunnel(proxy_host, int(proxy_port), host, port,
                       version=version, timeout=timeout)
    try:
        if https:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = sock  # reuse the already-tunnelled socket
        headers = {'Host': host}
        if user_agent:
            headers['User-Agent'] = user_agent
        conn.request('GET', path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode('utf-8', 'replace')
        return resp.status, body
    finally:
        sock.close()
