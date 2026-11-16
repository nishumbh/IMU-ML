"""Small CPython/MicroPython canonical JSON: ASCII, sorted keys, no floats.

Matches json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
for the supported JSON types. The returned str is signed as UTF-8 bytes.
"""


def _string(value):
    out = ['"']
    escapes = {8: '\\b', 9: '\\t', 10: '\\n', 12: '\\f', 13: '\\r',
               34: '\\"', 92: '\\\\'}
    for char in value:
        code = ord(char)
        if code in escapes:
            out.append(escapes[code])
        elif code < 32 or code > 126:
            if code <= 0xffff:
                out.append('\\u%04x' % code)
            else:
                code -= 0x10000
                out.append('\\u%04x\\u%04x' % (0xd800 + (code >> 10),
                                               0xdc00 + (code & 1023)))
        else:
            out.append(char)
    out.append('"')
    return ''.join(out)


def dumps(obj):
    if obj is None:
        return 'null'
    if obj is True:
        return 'true'
    if obj is False:
        return 'false'
    if isinstance(obj, str):
        return _string(obj)
    if type(obj) is int:
        return str(obj)
    if isinstance(obj, list):
        return '[' + ','.join(dumps(value) for value in obj) + ']'
    if isinstance(obj, dict):
        if any(not isinstance(key, str) for key in obj):
            raise ValueError('JSON object keys must be strings')
        return '{' + ','.join(_string(key) + ':' + dumps(obj[key])
                              for key in sorted(obj)) + '}'
    raise ValueError('unsupported canonical JSON type')
