"""Bounded HTTP/1.x download; no redirects, chunking or insecure TLS fallback.

TLS requires SNI, CERT_REQUIRED and a factory-installed DER CA. Some stock
MicroPython builds lack these APIs: those builds cannot do HTTPS updates.
Socket timeouts bound I/O, not a port's synchronous DNS implementation.
"""
import time
from system.storage import path, read_bytes

MAX_BUNDLE = 256 * 1024
MAX_HEADERS = 4096


def parse_url(url, iteration=1):
    if not isinstance(url, str) or len(url) > 1024:
        raise ValueError('invalid OTA URL')
    if any(ord(c) <= 32 or ord(c) >= 127 for c in url) or '#' in url or '\\' in url:
        raise ValueError('invalid URL character')
    if '://' not in url:
        raise ValueError('URL requires scheme')
    scheme, rest = url.split('://', 1)
    if scheme not in ('https', 'http') or (iteration == 2 and scheme != 'https'):
        raise ValueError('HTTPS required for iteration 2')
    slash = rest.find('/')
    question = rest.find('?')
    cuts = [v for v in (slash, question) if v >= 0]
    cut = min(cuts) if cuts else len(rest)
    authority, target = rest[:cut], rest[cut:]
    if not target:
        target = '/'
    elif target.startswith('?'):
        target = '/' + target
    if authority.count(':') > 1:
        raise ValueError('IPv6 literals not supported by prototype')
    parts = authority.split(':')
    host = parts[0]
    if not host or len(host) > 253 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in host):
        raise ValueError('invalid URL host')
    if any(not label or label.startswith('-') or label.endswith('-') or len(label) > 63
           for label in host.split('.')):
        raise ValueError('invalid host label')
    port = 443 if scheme == 'https' else 80
    if len(parts) == 2:
        if not parts[1] or any(c not in '0123456789' for c in parts[1]):
            raise ValueError('invalid URL port')
        port = int(parts[1])
    if not 1 <= port <= 65535:
        raise ValueError('invalid URL port')
    return scheme, host, port, target


def _now():
    if hasattr(time, 'ticks_ms'):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


class Deadline:
    def __init__(self, seconds):
        self.start = _now()
        self.milliseconds = int(seconds * 1000)

    def remaining(self):
        now = _now()
        elapsed = time.ticks_diff(now, self.start) if hasattr(time, 'ticks_diff') else now - self.start
        remaining = self.milliseconds - elapsed
        if remaining <= 0:
            raise OSError('operation timed out')
        return remaining / 1000


def receive(sock, count, deadline):
    sock.settimeout(deadline.remaining())
    method = getattr(sock, 'read', None) or sock.recv
    return method(count)


def send_all(sock, data, deadline):
    offset = 0
    method = getattr(sock, 'write', None) or sock.send
    while offset < len(data):
        sock.settimeout(deadline.remaining())
        count = method(data[offset:])
        if not count:
            raise OSError('short socket write')
        offset += count


def read_headers(sock, deadline, limit=MAX_HEADERS, include_size=False):
    data = b''
    while b'\r\n\r\n' not in data:
        if len(data) >= limit:
            raise ValueError('headers too large')
        block = receive(sock, min(256, limit - len(data)), deadline)
        if not block:
            raise ValueError('incomplete HTTP headers')
        data += block
    head, body = data.split(b'\r\n\r\n', 1)
    try:
        lines = head.decode('ascii').split('\r\n')
    except UnicodeError:
        raise ValueError('non-ASCII HTTP header')
    headers = {}
    for line in lines[1:]:
        if ':' not in line or line.startswith((' ', '\t')):
            raise ValueError('invalid header')
        key, value = line.split(':', 1)
        if not key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in key):
            raise ValueError('invalid header name')
        key = key.lower()
        if key in headers or any(ord(c) < 32 or ord(c) >= 127 for c in value):
            raise ValueError('duplicate or invalid header')
        headers[key] = value.strip()
    if include_size:
        return lines[0], headers, body, len(head) + 4
    return lines[0], headers, body


def content_length(headers, limit):
    if 'transfer-encoding' in headers or headers.get('content-encoding', 'identity') != 'identity':
        raise ValueError('encoded HTTP bodies not supported')
    text = headers.get('content-length', '')
    if not text or any(c not in '0123456789' for c in text) or len(text) > 9:
        raise ValueError('Content-Length required')
    length = int(text)
    if length > limit:
        raise ValueError('HTTP body too large')
    return length


def read_body(sock, initial, length, deadline):
    if len(initial) > length:
        raise ValueError('extra HTTP body bytes')
    body = bytearray(initial)
    while len(body) < length:
        block = receive(sock, min(1024, length - len(body)), deadline)
        if not block:
            raise ValueError('truncated HTTP body')
        body.extend(block)
    return bytes(body)


def get(url, iteration=1, root='/', limit=MAX_BUNDLE, timeout=30,
        socket_module=None, ssl_module=None):
    scheme, host, port, target = parse_url(url, iteration)
    if socket_module is None:
        import socket as socket_module
    deadline = Deadline(timeout)
    context = None
    if scheme == 'https':
        if ssl_module is None:
            import ssl as ssl_module
        # Missing attributes, unsupported cadata, or failed CA parsing abort.
        context = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl_module.CERT_REQUIRED
        if hasattr(context, 'check_hostname'):
            context.check_hostname = True
        context.load_verify_locations(cadata=read_bytes(path(root, 'config/ca.der'), 16384))
    sock = None
    raw = None
    try:
        addresses = socket_module.getaddrinfo(host, port, 0, socket_module.SOCK_STREAM)
        if not addresses:
            raise OSError('host not found')
        address = addresses[0]
        raw = socket_module.socket(address[0], address[1], address[2])
        sock = raw
        sock.settimeout(deadline.remaining())
        sock.connect(address[-1])
        if context is not None:
            sock = context.wrap_socket(raw, server_hostname=host)
        authority = host if port == (443 if scheme == 'https' else 80) else host + ':' + str(port)
        request = ('GET ' + target + ' HTTP/1.1\r\nHost: ' + authority +
                   '\r\nConnection: close\r\nAccept: application/json\r\n\r\n').encode('ascii')
        send_all(sock, request, deadline)
        status, headers, initial = read_headers(sock, deadline)
        parts = status.split(' ', 2)
        if len(parts) < 2 or parts[0] not in ('HTTP/1.0', 'HTTP/1.1') or parts[1] != '200':
            raise ValueError('OTA HTTP status is not 200')
        length = content_length(headers, min(limit, MAX_BUNDLE))
        return read_body(sock, initial, length, deadline)
    finally:
        if sock is not None:
            sock.close()
        if raw is not None and raw is not sock:
            raw.close()
