"""No optional crypto dependencies; HMAC-SHA256 using the port's hashlib."""
import hashlib
import binascii


def digest(data):
    return hashlib.sha256(data).digest()


def hex_digest(data):
    return binascii.hexlify(digest(data)).decode('ascii')


def constant_time_equal(left, right):
    # Lengths are public for tokens/digests, but compare every supplied byte.
    if isinstance(left, str):
        left = left.encode('utf-8')
    if isinstance(right, str):
        right = right.encode('utf-8')
    result = len(left) ^ len(right)
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else 0
        b = right[index] if index < len(right) else 0
        result |= a ^ b
    return result == 0


def hmac_sha256(key, message):
    if len(key) > 64:
        key = digest(key)
    key = key + b'\x00' * (64 - len(key))
    inner = bytes(value ^ 0x36 for value in key)
    outer = bytes(value ^ 0x5c for value in key)
    return digest(outer + digest(inner + message))


def hex_bytes(value, length):
    if not isinstance(value, str) or len(value) != length * 2:
        raise ValueError('invalid hex length')
    if any(char not in '0123456789abcdefABCDEF' for char in value):
        raise ValueError('invalid hex')
    return binascii.unhexlify(value)
